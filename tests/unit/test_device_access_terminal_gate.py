import pytest

from src.platform.device_access import governance, terminal_gate
from src.platform.device_access.contracts import DeviceAccessError
from src.platform.device_access.terminal_gate import TerminalGate, transcript


class Device:
    """A Comware-like CLI: the device edits the line; only Enter runs it."""

    closed = False

    def __init__(self, prompt="<R1>", outputs=None, questions=None, clears=True, cols=80):
        self.prompt, self.line, self.question = prompt, "", None
        self.outputs, self.questions, self.clears = outputs or {}, questions or {}, clears
        self.pending, self.sent, self.executed, self.cols = [prompt], [], [], cols

    def sendall(self, raw):
        self.sent.append(raw)
        for key in raw.decode():
            if key == "\t" and self.line and "display ".startswith(self.line):
                self.pending.append("display "[len(self.line) :])
                self.line = "display "
            elif key == "?":
                help_text = "?\r\n  display  Display current system information\r\n\r\n"
                self.pending.append(help_text + self.prompt + self.line)
            elif key in "\x05\x18\x15":
                if key != "\x05" and self.clears:
                    # Back to the end of the prompt, across wrapped rows too.
                    up = (len(self.prompt) + len(self.line)) // self.cols
                    back = f"\x1b[{up}A" if up else ""
                    self.pending.append(f"\r{back}\x1b[{len(self.prompt)}C\x1b[J")
                    self.line = ""
            elif key == "\r":
                line, self.line = self.line, ""
                if self.question:
                    self.executed.append(("answer", line))
                    self.question = None
                    self.pending.append("\r\nDone.\r\n" + self.prompt)
                elif line in self.questions:
                    self.executed.append(line)
                    self.question = self.questions[line]
                    self.pending.append("\r\n" + self.question)
                else:
                    self.executed.append(line)
                    output = self.outputs.get(line, "")
                    self.pending.append("\r\n" + (output + "\r\n" if output else "") + self.prompt)
            else:
                self.line += key
                self.pending.append(key)


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr(terminal_gate, "QUIET", 0)
    monkeypatch.setattr(terminal_gate, "SETTLE_LIMIT", 0)

    def build(device, *, cols=80, patterns=(r"display .*", r"system-view", r"save")):
        governance.save_policy(list(patterns), "admin")
        shown = []
        operation = governance.SSHOperation(
            "u", "device-access", "s1", "10.0.0.1", secrets=("lab-test-secret",)
        )
        instance = TerminalGate(device, operation, shown.append, cols=cols)
        instance.shown = shown
        settle(instance, device)
        return instance

    return build


def settle(gate, device):
    for _ in range(50):
        while device.pending:
            gate.received(device.pending.pop(0))
        gate.tick()
        if not gate.busy and not device.pending:
            return


def type_keys(gate, device, *frames):
    notices = []
    for frame in frames:
        notice = gate.typed(frame)
        if notice:
            notices.append(notice)
        settle(gate, device)
    return notices


def audit(kind):
    return governance.query_audit(kind=kind)["items"]


def test_device_does_completion_and_help_and_the_command_is_recorded(gate):
    device = Device(outputs={"display version": "H3C Comware Software, Version 7.1.064"})
    terminal = gate(device)
    type_keys(terminal, device, "d", "i", "s", "\t", "?", "v", "e", "r", "s", "i", "o", "n", "\r")
    assert device.executed == ["display version"]
    finished = audit("command_finished")
    assert [row["detail"]["command"] for row in finished] == ["display version"]
    assert finished[0]["status"] == "succeeded"
    assert finished[0]["detail"]["output"] == "H3C Comware Software, Version 7.1.064"


def test_typing_right_after_the_prompt_ends_the_last_record(gate, monkeypatch):
    device = Device(outputs={"display clock": "10:00:00 UTC"})
    terminal = gate(device)
    monkeypatch.setattr(terminal_gate, "QUIET", 60)  # the user types before any pause
    type_keys(terminal, device, "display clock\r", "display", "?", " clock\r")
    terminal.close()
    finished = audit("command_finished")
    assert [row["detail"]["command"] for row in finished] == ["display clock", "display clock"]
    assert [row["detail"]["output"] for row in finished] == ["10:00:00 UTC"] * 2


def test_denied_command_is_cleared_on_the_device_and_never_runs(gate):
    device = Device()
    terminal = gate(device)
    type_keys(terminal, device, "reboot", "\r")
    assert device.executed == [""]  # only the empty line after clearing
    assert b"\x05\x18" in device.sent
    assert "已拦截「reboot」" in "".join(terminal.shown)
    denied = audit("command_denied")
    assert denied[0]["detail"]["command"] == "reboot"
    assert audit("command_requested") == []


def test_cisco_style_prompt_is_cleared_with_ctrl_u(gate):
    device = Device(prompt="R1#")
    terminal = gate(device)
    type_keys(terminal, device, "reload", "\r")
    assert b"\x05\x15" in device.sent and device.executed == [""]


def test_answers_to_device_questions_are_not_commands(gate):
    device = Device(questions={"save": "Continue? [Y/N]:"})
    terminal = gate(device)
    type_keys(terminal, device, "save", "\r", "y", "\r")
    assert device.executed == ["save", ("answer", "y")]
    finished = audit("command_finished")
    assert [row["detail"]["command"] for row in finished] == ["save"]
    assert "Continue? [Y/N]:y" in finished[0]["detail"]["output"]


def test_long_commands_wrap_onto_several_rows(gate):
    command = "display interface GigabitEthernet1/0 brief description"
    device = Device()
    terminal = gate(device, cols=20)
    type_keys(terminal, device, *command, "\r")
    assert device.executed == [command]
    assert audit("command_requested")[0]["detail"]["command"] == command


def test_long_denied_command_is_still_checked_when_wrapped(gate):
    device = Device(cols=20)
    terminal = gate(device, cols=20)
    type_keys(terminal, device, *"undo interface GigabitEthernet1/0", "\r")
    assert device.executed == [""]


def test_multiline_paste_is_not_sent(gate):
    device = Device()
    terminal = gate(device)
    notices = type_keys(terminal, device, "display version\rreboot\r")
    assert notices and device.sent == []


def test_pasted_line_with_enter_is_checked(gate):
    device = Device()
    terminal = gate(device)
    type_keys(terminal, device, "reboot\r")
    assert device.executed == [""]
    type_keys(terminal, device, "display clock\r")
    assert device.executed == ["", "display clock"]


def test_line_that_cannot_be_cleared_closes_the_terminal(gate):
    device = Device(clears=False)
    terminal = gate(device)
    terminal.typed("reboot")
    settle(terminal, device)
    terminal.typed("\r")
    with pytest.raises(DeviceAccessError) as error:
        settle(terminal, device)
    assert error.value.failure_code == "terminal_unsafe"
    assert device.executed == []


def test_unfinished_command_is_recorded_when_the_session_ends(gate):
    device = Device()
    terminal = gate(device)
    terminal.typed("display logbuffer")
    settle(terminal, device)
    terminal.typed("\r")
    terminal.tick()  # checks the line and forwards Enter
    device.pending.clear()  # the device is still printing
    terminal.received("\r\nlog line 1")
    terminal.close()
    finished = audit("command_finished")[0]
    assert finished["detail"]["failure_code"] == "terminal_closed"
    assert finished["detail"]["output_complete"] is False


def test_transcript_drops_pager_escapes_and_the_next_prompt():
    raw = (
        "\r\n line 1\r\n  ---- More ----\x1b[16D                \x1b[16D line 2\r\n"
        " --More-- \b\b\b\b\b\b\b\b\b\b          \b\b\b\b\b\b\b\b\b\b line 3\r\n<R1>dis"
    )
    assert transcript(raw) == " line 1\n line 2\n line 3"
