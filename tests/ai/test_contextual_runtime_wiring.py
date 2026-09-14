from hina_bot.ai.chat_llm import LLM as ChatLLM
from hina_bot.ai.runtime_llm import LLM as RuntimeLLM
from hina_bot.discord.runtime_entry import LLM as EntryLLM


def test_runtime_llm_owns_followup_routing_without_wrapper_class():
    assert issubclass(RuntimeLLM, ChatLLM)
    assert EntryLLM is RuntimeLLM
    assert RuntimeLLM.answer is not ChatLLM.answer
