import json

from hina_bot.tooling.retrieval_v2_rollout_eval import main


def test_packaged_rollout_eval_fixture_validates_without_api_key(capsys):
    assert main(["--validate-only"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert report["cases"] >= 8
    assert {
        "profile",
        "relation",
        "factual_semantic",
        "ambient",
        "reaction",
        "unrelated",
    } <= set(report["categories"])
