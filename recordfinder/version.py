VERSION = "1.17.1"


def parse(v: str) -> tuple:
    """"1.6.0" -> (1, 6, 0); anything unreadable sorts first."""
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except ValueError:
        return (0,)
