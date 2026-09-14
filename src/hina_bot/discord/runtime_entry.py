from hina_bot.ai.runtime_llm import LLM

from . import web_bot


def main():
    web_bot.LLM = LLM
    web_bot.main()
