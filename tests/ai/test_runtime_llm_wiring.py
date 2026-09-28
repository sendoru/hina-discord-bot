from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.llm import LLM as BaseLLM
from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.ai.request_assembly import RequestAssembler
from hina_bot.ai.runtime_llm import LLM as RuntimeLLM


def test_runtime_pipeline_has_named_responsibility_layers():
    assert issubclass(RuntimeLLM, InformationPipeline)
    assert issubclass(InformationPipeline, RequestAssembler)
    assert InformationPipeline in RuntimeLLM.__mro__
    assert RequestAssembler in RuntimeLLM.__mro__



def test_base_llm_does_not_keep_shadowed_generation_paths():
    assert "answer" not in BaseLLM.__dict__
    assert "summarize" not in BaseLLM.__dict__
    assert "summarize_shared" not in BaseLLM.__dict__
    assert "answer" in RequestAssembler.__dict__
    assert "summarize" in MemorySummaryMixin.__dict__
    assert "summarize_shared" in MemorySummaryMixin.__dict__
