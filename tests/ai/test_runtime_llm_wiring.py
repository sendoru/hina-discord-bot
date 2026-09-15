from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.request_assembly import RequestAssembler
from hina_bot.ai.runtime_llm import LLM as RuntimeLLM


def test_runtime_pipeline_has_named_responsibility_layers():
    assert issubclass(RuntimeLLM, InformationPipeline)
    assert issubclass(InformationPipeline, RequestAssembler)
    assert InformationPipeline in RuntimeLLM.__mro__
    assert RequestAssembler in RuntimeLLM.__mro__

