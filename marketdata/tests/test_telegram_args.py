from md import telegram


def test_args_parsing():
    assert telegram._args([], "4h") == ("BTC", "4h")
    assert telegram._args(["eth", "4s"], "24h") == ("ETH", "4h")
    assert telegram._args(["arsiv"], "24h") == ("BTC", "24h")
    assert telegram._args(["sol", "dosya"], "24h") == ("SOL", "24h")
    assert telegram._args(["7g"], "24h") == ("BTC", "7d")


def test_telegram_task_registry():
    assert telegram.ALL == [telegram.poll, telegram.alerts_loop, telegram.reports_loop,
                            telegram.deliver_card, telegram.options_alerts]
    assert all(callable(task) for task in telegram.ALL)


def test_startup_task_registry():
    from md import ws, rest, orderbook
    tasks = [*ws.ALL, *rest.ALL, orderbook.poll, *telegram.ALL]
    assert tasks and all(callable(task) for task in tasks)
