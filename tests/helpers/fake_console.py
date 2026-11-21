"""A stand-in for the terminal: typed answers in order, hidden (getpass) answers in order, and whether it's a terminal.

    console = FakeConsole(["s", "1", ""], secrets=["my-key"], tty=True)
    cli("setup", console=console)
    console.prompts  # every prompt shown, in order

Running out of answers raises EOFError, as input() does at the end of input, so a test that expects fewer
questions than were asked fails loudly instead of hanging.
"""


class FakeConsole:
    def __init__(self, answers=(), secrets=(), tty=False, stdin=""):
        self.answers = list(answers)
        self.secrets = list(secrets)
        self.tty = tty
        self.stdin = stdin
        self.prompts: list[str] = []
        self.secret_prompts: list[str] = []
        self.stdin_reads = 0

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def secret(self, prompt: str) -> str:
        self.secret_prompts.append(prompt)
        if not self.secrets:
            raise EOFError
        return self.secrets.pop(0)

    def interactive(self) -> bool:
        return self.tty

    def read_all(self) -> str:
        self.stdin_reads += 1
        return self.stdin
