from hina_bot.ai.contextual_chat_llm import LLM as ContextualChatLLM
from hina_bot.ai.contextual_runtime_llm import LLM as ContextualRuntimeLLM
from hina_bot.ai.runtime_llm import LLM as RuntimeLLM
from hina_bot.discord.runtime_entry import LLM as EntryLLM


def test_contextual_runtime_composes_existing_runtime_and_routing_layers():
    assert issubclass(ContextualRuntimeLLM, RuntimeLLM)
    assert issubclass(ContextualRuntimeLLM, ContextualChatLLM)
    assert EntryLLM is ContextualRuntimeLLM
