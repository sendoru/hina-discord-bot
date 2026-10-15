"""Regression checks for Hina-centered cross-school character contacts."""

from hina_bot.core.compact_lore import compile_compact_lore
from hina_bot.core.lore import LoreIndex


def test_documented_contacts_preserve_perspective_in_full_lore():
    lore = LoreIndex.load()
    ids = {row["id"] for row in lore.records}
    assert "canon.serenade.hina_kirara_erika.casual_contacts" in ids
    assert "canon.serenade.hina_iroha.shares_information" in ids
    assert "canon.serenade.hina_satsuki.hears_hypnosis_report" in ids
    assert "canon.final.hina_alice.fights_together" in ids
    assert "canon.eden.hina_hinata.joint_protection" in ids

    report = next(row for row in lore.records
                  if row["id"] == "canon.serenade.hina_satsuki.hears_hypnosis_report")
    assert report["knowledge"] == "reported"
    assert report["fact_type"] == "fact_reported"

    compiled = compile_compact_lore(lore, include_community=False)
    assert "키라라·에리카" in compiled.text
    assert "게임개발부의 아리스" in compiled.text
    assert "이로하는 온천개발부의 거점 정보를 알려 주었다" in compiled.text
    assert "전해 들음; 발언/전언" in compiled.text
    assert "rojiuragame.com" not in compiled.text
    assert "bluearchive.fandom.com" not in compiled.text
    assert compiled.excluded_audience_items > 0
