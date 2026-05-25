from chat.prompt import build_system_message, detect_language


def test_detect_language_english():
    assert detect_language("What is the stock price of BRACBANK?") == "en"


def test_detect_language_bengali():
    assert detect_language("ব্র্যাক ব্যাংকের শেয়ার দাম কত?") == "bn"


def test_detect_language_mixed():
    assert detect_language("BRACBANK এর price কত?") == "bn"


def test_build_system_message_english():
    from langchain_core.messages import SystemMessage
    msg = build_system_message(lang="en")
    assert isinstance(msg, SystemMessage)
    assert "DSE" in msg.content
    assert "Dhaka Stock Exchange" in msg.content


def test_build_system_message_bengali():
    from langchain_core.messages import SystemMessage
    msg = build_system_message(lang="bn")
    assert isinstance(msg, SystemMessage)
    assert "ঢাকা স্টক এক্সচেঞ্জ" in msg.content


def test_build_system_message_ticker_context():
    msg = build_system_message(lang="en", ticker_context="BRACBANK is trading at 45 BDT")
    assert "BRACBANK" in msg.content
