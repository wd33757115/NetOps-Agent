from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.apps.topology.backend.collectors import collect, netconf
from src.apps.topology.backend.collectors.netconf_identity import read_netconf_identity
from src.apps.topology.backend.collectors.profiles import load_profiles
from src.platform.device_access.contracts import AccessContext, AccessGrant, ConnectionProfile


IETF = "urn:ietf:params:xml:ns:yang:ietf-hardware"
OC = "http://openconfig.net/yang/platform"


class Session:
    def __init__(self, capabilities, replies):
        self.capabilities = capabilities
        self.replies = replies
        self.reads = []

    def get(self, subtree):
        self.reads.append(subtree)
        if "ietf-hardware" in subtree:
            return self.replies.get("ietf-hardware", "<data/>")
        return self.replies.get("openconfig-platform", "<data/>")


def test_platform_identity_tries_ietf_hardware_before_openconfig():
    session = Session(
        (
            f"{IETF}?module=ietf-hardware&revision=2018-03-13",
            f"{OC}?module=openconfig-platform&revision=2018-11-21",
            "urn:huawei:yang:huawei-ifm?module=huawei-ifm&revision=2020-01-01",
        ),
        {
            "ietf-hardware": "<data xmlns='urn:ietf:params:xml:ns:netconf:base:1.0'><hardware xmlns='urn:ietf:params:xml:ns:yang:ietf-hardware'><component><name>chassis</name><mfg-name>Other Vendor</mfg-name><model-name>Unknown</model-name><software-rev>9.0</software-rev></component></hardware></data>",
            "openconfig-platform": f"<data xmlns='urn:ietf:params:xml:ns:netconf:base:1.0'><components xmlns='{OC}'><component><name>chassis</name><state><mfg-name>Huawei</mfg-name><model-name>AR6700</model-name><software-version>YunShan OS 1.25.1.1</software-version></state></component></components></data>",
        },
    )
    result, modules = read_netconf_identity(session)
    assert ["ietf-hardware" in item for item in session.reads] == [True, False]
    assert ["openconfig.net/yang/platform" in item for item in session.reads] == [False, True]
    assert result.identity.profile_id == "huawei-vrp"
    assert result.source == "openconfig-platform"
    assert result.model == "AR6700" and result.software == "YunShan OS 1.25.1.1"
    assert set(modules) == {"ietf-hardware", "openconfig-platform", "huawei-ifm"}


def test_vendor_private_module_is_the_identity_fallback():
    session = Session(
        (
            f"{OC}?module=openconfig-platform&revision=2018-11-21",
            "urn:huawei:yang:huawei-ifm?module=huawei-ifm&revision=2020-01-01",
        ),
        {"openconfig-platform": "<data/>"},
    )
    result, _ = read_netconf_identity(session)
    assert result.identity.profile_id == "huawei-vrp"
    assert result.identity.confidence == "medium"
    assert result.source == "vendor_private_yang"
    assert result.identity.warnings == ("model_and_software_not_observed",)


@pytest.mark.parametrize(
    ("problem_table", "uses_netconf"),
    [(None, True), ("nat-policy-rule", True), ("lldp-neighbor", False)],
)
def test_netconf_identity_keeps_cli_fallback_scoped_to_topology_tables(
    monkeypatch, problem_table, uses_netconf
):
    from src.apps.topology.backend.collectors.identify import Identity
    from src.apps.topology.backend.collectors.netconf_identity import PlatformEvidence

    chosen = Identity(
        vendor="Huawei",
        os_family="vrp",
        profile_id="huawei-vrp",
        confidence="high",
        status="identified",
        evidence=({"source": "openconfig-platform"},),
    )
    records = {
        "interface": [
            {"name": "GE0/0/0", "enabled": True, "oper-status": "up"}
        ],
        "route": [],
    }

    class Session:
        capabilities = (f"{OC}?module=openconfig-platform&revision=2018-11-21",)

    class Service:
        def __init__(self, **kwargs):
            pass

        @contextmanager
        def netconf(self, *args, **kwargs):
            yield Session(), lambda: None

    monkeypatch.setattr(collect, "DeviceAccessService", Service)
    monkeypatch.setattr(
        "src.apps.topology.backend.collectors.netconf_identity.read_netconf_identity",
        lambda session, guard: (
            PlatformEvidence(chosen, "Huawei", "AR6700", "YunShan OS 1.25.1.1", "openconfig-platform"),
            {"openconfig-platform": "2018-11-21"},
        ),
    )
    monkeypatch.setattr(netconf, "standard_selection", lambda *args: SimpleNamespace(ref="huawei_yunshan@abc"))
    monkeypatch.setattr(
        netconf,
        "read_standard",
        lambda *args: {
            "mapping": "huawei_yunshan@abc",
            "records": records,
            "failed": {},
            "problem_count": int(problem_table is not None),
            "problem_count_by_table": {problem_table: 1} if problem_table else {},
        },
    )
    access = SimpleNamespace(
        target_resolver=lambda _: SimpleNamespace(),
        profile_resolver=lambda _: ConnectionProfile("conn", "admin", "secret"),
        driver=SimpleNamespace(name="paramiko"),
        admission=object(),
        netconf_connector=object(),
    )
    context = AccessContext(
        "actor", "topology", (AccessGrant("node", "conn", action="device.netconf"),)
    )

    result, fallback = collect._netconf_first(
        access,
        context,
        "node",
        "conn",
        ConnectionProfile("conn", "admin", "secret"),
        "topology",
        load_profiles(),
        lambda: 30,
        lambda: True,
        None,
    )
    if uses_netconf:
        assert fallback is None
        assert result.driver == "netconf" and result.identity.profile_id == "huawei-vrp"
        assert result.parsed["lldp"].status == "complete"
        assert result.standard["mapping"] == "huawei_yunshan@abc"
        assert result.standard["records"]["lldp-neighbor"] == []
    else:
        assert result is None
        assert fallback["reason"] == "standard_data_incomplete"
