from md import telegram


def test_args_parsing():
    assert telegram._args([], "4h") == ("BTC", "4h")
    assert telegram._args(["eth", "4s"], "24h") == ("ETH", "4h")
    assert telegram._args(["arsiv"], "24h") == ("BTC", "24h")
    assert telegram._args(["sol", "dosya"], "24h") == ("SOL", "24h")
    assert telegram._args(["7g"], "24h") == ("BTC", "7d")
