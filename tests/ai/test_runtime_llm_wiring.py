from hina_bot.ai.chat_llm import LLM as ChatLLM
from hina_bot.ai.runtime_llm import LLM as RuntimeLLM


def test_runtime_llm_inherits_information_routing_layer():
    assert issubclass(RuntimeLLM, ChatLLM)
    assert ChatLLM in RuntimeLLM.__mro__
    assert RuntimeLLM._web_search_mode is ChatLLM._web_search_mode
