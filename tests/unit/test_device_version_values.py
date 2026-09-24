"""Synthetic CLI fixtures exercise the approved slot contract, not vendor acceptance."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib
import io

import pytest
import textfsm
import yaml

from src.core.check_items.catalog import build_check_item_definition, load_catalog_defaults, validate_check_item_definition
from src.core.check_items.parser_capability import build_parser_field_naming_context
from src.core.check_items.value_contract import normalize_value_contract, same_name_fields
from src.core.check_items.command_adapter import execute_adapter
from src.core.check_items.adapter_projection_migration import generate_projection_candidate
from src.apps.rule_governance.backend.generators.textfsm_generator.scripts.spec_suggester import _normalize_spec
from src.core.parser_generation import ParserSpec, ParserSample, SampleRole, TextFSMValidationBackend, validate_template_runtime_compatibility

SOURCE = Path('src/apps/rule_governance/backend/check_items/assets/device_version_values.yaml')
REQUIRED = {'VENDOR', 'SOFTWARE_VERSION', 'BOOT_IMAGE', 'SYSTEM_IMAGE', 'SLOT', 'UPTIME', 'BOARD_TYPE'}
TEMPLATE = r'''Value Filldown VENDOR (\S+)
Value Filldown SOFTWARE_VERSION (.+)
Value Filldown BOOT_IMAGE (\S+)
Value Filldown SYSTEM_IMAGE (\S+)
Value Required SLOT (\S+)
Value UPTIME (.+)
Value BOARD_TYPE (\S+)
Value DRAM (.+)
Value FLASH (.+)
Value PATCH_VERSION (.+)
Value REBOOT_REASON (.+)
Value List SUBSLOT (\S+)
Value List SUBSLOT_INFO (.+)

Start
  ^Vendor: ${VENDOR}$$
  ^Software for all slots: ${SOFTWARE_VERSION}$$
  ^Boot for all slots: ${BOOT_IMAGE}$$
  ^System for all slots: ${SYSTEM_IMAGE}$$
  ^Slot ${SLOT}: ${BOARD_TYPE}$$ -> Board

Board
  ^Uptime: ${UPTIME}$$
  ^DRAM: ${DRAM}$$
  ^Flash: ${FLASH}$$
  ^Patch: ${PATCH_VERSION}$$
  ^Reboot: ${REBOOT_REASON}$$
  ^SubSlot ${SUBSLOT}: ${SUBSLOT_INFO}$$
  ^End Slot$$ -> Record Start
'''
CLI = '''Vendor: H3C
Software for all slots: Version 7.1.070, Release 8307P62
Boot for all slots: s5590ei-cmw710-boot-r8307p62.bin
System for all slots: s5590ei-cmw710-system-r8307p62.bin
Device uptime: 9 weeks, 1 day, 0 hours, 54 minutes
Slot 1: S5590-48T4XC-EI
Uptime: 9 weeks, 1 day, 0 hours, 54 minutes
DRAM: 2048M bytes
Flash: 3687M bytes
Patch: R8307P62H01
Reboot: ColdReboot
SubSlot 0: 48GE+4SFP
SubSlot 1: Expansion module
End Slot
Slot 2: S5590-48T4XC-EI
Uptime: 2 hours
End Slot
'''


def item():
    return build_check_item_definition(yaml.safe_load(SOURCE.read_text(encoding='utf-8')),
                                       defaults=load_catalog_defaults()['check_item'])


def fields():
    return {**{r['canonical_field']: {'required': r['required'], 'type': 'text'}
               for r in item().canonical_contract['observations']},
            'SLOT': {'required': True, 'type': 'text'}}


def parse(text=CLI):
    parser = textfsm.TextFSM(io.StringIO(TEMPLATE))
    return [dict(zip(parser.header, row)) for row in parser.ParseText(text)]


def execute(rows):
    check = item()
    target = {'id': check.id, 'version_id': check.version_id, 'canonical_contract': check.canonical_contract}
    proposal = generate_projection_candidate({'selected_check_item': target,
        'parser': {'template_id': 'test', 'source_record_set': 'default', 'source_fields': list(fields())}})
    assert proposal['success'], proposal
    return execute_adapter(proposal['proposal'], record_sets={'default': rows},
                           check_items={check.id: target}, device_id='device-test', adapter_version_id='test')[0]


def test_thirteen_exact_values_and_seven_required():
    check = item()
    validate_check_item_definition(check)
    assert len(fields()) == 13
    assert {name for name, spec in fields().items() if spec['required']} == REQUIRED
    assert check.canonical_contract['identity_dimensions'] == ['SLOT']
    assert same_name_fields(check.canonical_contract, fields())['SOFTWARE_VERSION'] == {'source': 'SOFTWARE_VERSION'}
    with pytest.raises(ValueError, match='undeclared'):
        same_name_fields(check.canonical_contract, [name.lower() for name in fields()])


def test_strict_prompt_context_and_model_spec_preserve_uppercase():
    check = item()
    from dataclasses import replace
    from src.core.check_items.catalog import load_catalog
    catalog = replace(load_catalog(), check_items=(check,))
    context = build_parser_field_naming_context(vendor='H3C', model='S5590', family=None,
        profile='S5590', command='display version', check_item_id=check.id, catalog=catalog)
    assert set(context['preferred_value_names']) == set(fields())
    normalized = _normalize_spec({'parser_spec': {'entity': 'device_slot', 'primary_keys': ['SLOT'],
        'fields': fields()}}, field_naming_context=context)
    assert set(normalized['fields']) == set(fields())
    assert normalized['primary_keys'] == ['SLOT']


def test_slot_records_inherit_explicit_common_fields_keep_children_and_board_uptime():
    rows = parse()
    assert len(rows) == 2
    assert [row['SLOT'] for row in rows] == ['1', '2']
    assert rows[1]['UPTIME'] == '2 hours'
    assert rows[1]['SOFTWARE_VERSION'] == rows[0]['SOFTWARE_VERSION']
    assert rows[0]['SUBSLOT'] == ['0', '1']
    assert rows[0]['SUBSLOT_INFO'] == ['48GE+4SFP', 'Expansion module']
    assert rows[1]['SUBSLOT'] == [] and rows[1]['PATCH_VERSION'] == ''
    batch = execute(rows)
    assert batch.quality_status == 'complete', batch.errors
    assert batch.records[0]['DRAM'] == '2048M bytes'
    assert batch.records[0]['SUBSLOT'] == ['0', '1']
    spec = ParserSpec.from_dict({'entity': 'device_slot', 'primary_keys': ['SLOT'], 'fields': fields()})
    report = validate_template_runtime_compatibility(TEMPLATE, spec,
        (ParserSample(sample_id='test', raw_text=CLI, role=SampleRole.GENERATION),), TextFSMValidationBackend())
    assert report.passed, report.diagnostics


@pytest.mark.parametrize('missing', sorted(REQUIRED))
def test_each_missing_required_value_marks_data_incomplete(missing):
    rows = parse()
    rows[0][missing] = ''
    batch = execute(rows)
    assert batch.quality_status == 'partial'
    assert any(missing in error for error in batch.errors), batch.errors


@pytest.mark.parametrize('patch', [
    {'SUBSLOT_INFO': ['one only']},
    {'SUBSLOT': ['0', '0']},
    {'SUBSLOT_INFO': ['', 'info']},
    {'SUBSLOT': '0'},
    {'VENDOR': 'unknown'},
])
def test_invalid_child_pair_or_unavailable_required_value_fails(patch):
    rows = parse(); rows[0].update(patch)
    batch = execute(rows)
    assert batch.quality_status == 'partial' and batch.errors


def test_uptime_does_not_become_change_event_when_enabling_field_changes():
    contract = deepcopy(item().canonical_contract)
    contract['change_events'] = True
    normalized = normalize_value_contract(contract)
    uptime = next(r for r in normalized['observations'] if r['canonical_field'] == 'UPTIME')
    assert uptime['event_on_change'] is False
    assert 'change' not in uptime['assessment_roles']


def test_production_parser_runtime_keeps_slot_and_child_lists():
    from src.core.rule_control.command_autopilot_steps import parse_samples
    template = SimpleNamespace(id='test', vendor='H3C', model='S5590', profile='S5590', family='Comware7',
        version='test', category='device_version', canonical_command='display version',
        template_text=TEMPLATE, asset_path='', sha256=hashlib.sha256(TEMPLATE.encode()).hexdigest(),
        validation_json={'category': 'device_version', 'entity_type': 'device_slot',
                         'primary_keys': ['SLOT'], 'required_fields': sorted(REQUIRED)})
    result = parse_samples(template, [{'id': 'test', 'sanitization_status': 'sanitized', 'sanitized_text': CLI}])
    rows = result[0]['record_sets']['default']
    assert len(rows) == 2
    assert rows[0]['SUBSLOT'] == ['0', '1']
