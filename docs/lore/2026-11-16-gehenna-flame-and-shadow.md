# 제2부 Vol.1 《불꽃과 그림자》 — 게헨나 설정 조사 및 lore 반영 기록

연결: [#514](https://github.com/sendoru/hina-discord-bot/issues/514) · [PR #516](https://github.com/sendoru/hina-discord-bot/pull/516) · 2026-11-16

> **검증 수준:** 한국 서버의 스토리 공개 여부는 [넥슨 11/10 업데이트 공지](https://forum.nexon.com/bluearchive/board_view?board=1076&thread=3566313)로 확인했습니다. 장면의 세부 내용은 **영어판 게임 대사 전문을 게재한 비공식 전사**를 직접 읽고, 일본어판 게임 화면이 담긴 플레이 영상의 화별 위치와 일본어 스토리 요약을 별도로 확인해 대조했습니다. **한국어 게임 클라이언트 원문 및 일본어 영상의 화면별 대사를 직접 전수 대조한 상태는 아닙니다.** `confidence=crosschecked`를 유지하며 `verified`로 올리지 않습니다.

## 범위와 출처

- 한국 서버 공개 대상: 메인 스토리 제2부 Vol.1 《불꽃과 그림자》 1장 「게헨나를 다시 위대하게!」 1~17화, 2장 「우리는 불꽃이 되어」 1~21화 (2026-11-10).
- 일본 서버 선행 자료는 **동일 에피소드의 내용 확인**에만 사용합니다. 한국 서버 미공개 후속 전개는 포함하지 않습니다.
- 2026-11-10에 함께 업데이트된 《출항! 만마선 ~마코토 의장님의 화려한 피서~》는 **메인 스토리와 별개의 이벤트**입니다.
- 비공식 전사 사이트 gacha.gg에는 대상 이야기가 **Act 2 Vol. 2**로 분류되어 있습니다. 저장소에서는 한국 서버의 **제2부 Vol.1** 표기를 유지하고, **장/화 제목 및 등장 장면**으로 동일 작품 여부를 대조했습니다. 사이트의 볼륨 숫자를 그대로 lore로 옮기지 마세요.

| 자료 | 확인한 용도 | 검증 등급 및 주의 |
| --- | --- | --- |
| [넥슨 공식 11/10 업데이트 공지](https://forum.nexon.com/bluearchive/board_view?board=1076&thread=3566313) | 한섭 공개 범위 | 공식 한국 서버 공지 |
| [일본어판 1장 전체 플레이 영상](https://www.youtube.com/watch?v=a24TQrLDXmM), [2장 전체 플레이 영상](https://www.youtube.com/watch?v=TuTITHcgU1E) | 게임 화면과 화별 타임스탬프, 차후 일본어 원문 확인 위치 | 실제 일본어 게임 화면 재생 기록이지만 *대사 프레임별 직접 대조는 미완료* |
| [영어판 1장 2화 대사](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-1-episode-2), [6화 대사](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-1-episode-6), [16화](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-1-episode-16), [17화](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-1-episode-17) | 히나의 최초 대응·NK 울트라 발동·이상행동 | 영어판 대사 전사; 공식 서비스의 웹 원문이 아닌 비공식 미러 |
| [영어판 2장 1화 대사](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-2-episode-1), [17화](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-2-episode-17), [18화](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-2-episode-18), [19화](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-2-episode-19), [21화](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-2-episode-21) | 뇌제 시대 설명, 후배들의 설득, 쇼코 폭로, 사과·사임, 선생과 성찰 | 영어판 대사 전사; 세부 번역 및 화자 확인에는 일본어 게임 대사 재검토 가능 |
| [일본어판 1장 요약](https://pomzero.hatenablog.com/entry/2026/08/16/165516), [2장 요약](https://pomzero.hatenablog.com/entry/2026/08/22/230927) | 큰 사건 흐름과 해석 오류 교차 점검 | 개인의 2차 요약·해석, 개별 대사 근거로 대체하지 않음 |
| [기존 히나 접점 조사](2026-10-15-expanded-character-contacts.md) | 기존 canon 중복 및 갈등 관계 경계 | 저장소의 기존 교차 검토 |

`kr_release=confirmed`는 **에피소드가 한국 서버에 나왔다는 사실**을 나타내며, 개별 한국어 대사를 검증했다는 뜻은 아닙니다. 전사 자료 역시 모든 감정·어휘의 뉘앙스를 원어로 보장하지 않습니다.

## 장면별 교차 검토 (대사 확인)

| 정확한 장면 | 확인할 수 있는 최소 사건 | 히나가 아는 범위 / 주의 |
| --- | --- | --- |
| 1장 2화 ([영어 대사](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-1-episode-2)) | 마유미(위원장)·카렌(부위원장)·쇼코(서기)가 히나·아코·치나츠·마코토 앞에서 자신들을 게헨나재흥위원회로 직접 소개하며 학원 개혁 목적을 밝힘 | **히나에게 위원회의 존재와 구성원은 이미 직접 알려져 있음.** 이후 6화의 구체적인 구역 계획 전달 방식과 구별해야 함 |
| 1장 6화 「교섭」 ([영어 대사](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-1-episode-6), [일본어 영상 1:15:26](https://www.youtube.com/watch?v=a24TQrLDXmM&t=4526s)) | 재흥위원회는 마코토에게 트리거 해피 존 활동의 임시 승인을 얻음. **앞서 위원회와 직접 대면한 히나가, 구체적인 구역 계획만은 선생을 통해 전달받고** 해당 구역의 선도부 불간섭을 수용함 | 후배들의 개선 의지를 존중하되 의문과 경계를 남김. 쇼코의 숨은 속내를 당시부터 알고 있지는 않음 |
| 1장 16~17화 ([16화](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-1-episode-16), [17화](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-1-episode-17)) | 방송 이후 히나와 마코토가 일치된 키보토스 정복 정책을 주장하고 선생·후배들이 변화를 알아차림 | 조작 중 발언을 평소의 자유로운 의사라고 확정하면 안 됨 |
| 2장 1화 ([대사](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-2-episode-1)) | 사츠키는 뇌제의 강제적 정신조작 연구가 약 2년 전 시작되었고 당시 마코토·사츠키 등이 반대 세력에 참여했으며 자료를 폐기했다고 **설명함** | 설명에 히나는 부재. 사츠키의 과거 세부 정보·작동 원리에 대한 추측을 히나의 확정 지식으로 넣지 않음. 사츠키 고유 최면과 뇌제의 NK 울트라는 구분 |
| 2장 17화 ([대사](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-2-episode-17), [일본어 영상 4:15:49](https://www.youtube.com/watch?v=TuTITHcgU1E&t=15349s)) | 이오리·치나츠는 히나가 힘이 있어도 함부로 휘두르지 않고 선을 지키는 모습을 존경해 선도부에 참여했다고 밝히며 저항함 | 선도부 후배들과 히나의 직접 경험. 이들이 히나에게 맞선 행위는 배신이 아니라 **히나에게 배운 원칙을 지키는 행동** |
| 2장 18화 ([대사](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-2-episode-18), [일본어 영상 4:33:50](https://www.youtube.com/watch?v=TuTITHcgU1E&t=16430s)) | 쇼코는 자신이 뇌제의 추종자로서 사건을 주도했다고 고백. NK 울트라의 후속 위험과 해제에 관한 전개 | 쇼코의 발언은 히나가 정신조작 상태인 동안 나옴. 회복 후 히나가 당시 내용을 모두 정확히 기억한다는 근거는 없음 |
| 2장 19화 ([대사](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-2-episode-19), [일본어 영상 5:10:31](https://www.youtube.com/watch?v=TuTITHcgU1E&t=18631s)) | 히나가 전체 학생에게 공개 사과하고 선도부장 사임을 선언하려 함. 마코토가 중단시키고 자신이 책임을 지겠다고 선언. 히나가 이어 이오리·치나츠에게 직접 사과하고 후배들의 용기를 인정함 | 마코토의 행동은 사건 속 도움이지 평소 갈등 종료 증명이 아님. 히나는 정신조작 중 후배를 공격한 감각과 후배의 표정을 기억한다고 밝힘 |
| 2장 20화 ([대사](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-2-episode-20)) | 카스미는 과거 자신과 연관된 보덴사츠(Bodensatz)를 떠올리며 사건의 방식에 유사성을 느낌 | 카스미의 **의심**이며 히나가 직접 들었다는 근거는 없음. 보덴사츠와 폭풍이 동일 조직이라는 근거도 없음 |
| 2장 21화 ([대사](https://www.gacha.gg/blue-archive/story/act-2-volume-2-chapter-2-episode-21), [일본어 영상 5:57:06](https://www.youtube.com/watch?v=TuTITHcgU1E&t=21426s)) | 히나는 선생에게 조작 중 힘을 행사하는 일이 편하고 즐겁게 느껴졌던 **자기 감각**을 고백하며 그것이 자신의 본성인지 두려워함. 선생과의 대화 뒤 자신이 어떤 방향으로 나아갈지 선택함. 이어 쇼코가 선생에게 배후 조직 **폭풍(嵐·STORM)**의 이름을 전하고, 히나와 마코토도 **직접 폭풍의 위협을 논의**함 | '모든 행동은 세뇌였으니 아무 감정도 못 느꼈다'도 부정확. 반대로 '본래 잔혹한 성격'이라고 확정하거나 **사건 직후 불안을 일상 감정으로 고정**하지도 않음. 히나는 폭풍의 존재를 인지하지만 **쇼코가 선생에게 한 모든 말을 들은 것은 아님** |

**과거와 현재를 구분:** 뇌제 통치와 반뇌제 운동은 이야기 내 약 2년 전입니다. 이를 2026년 게임 업데이트 시점과 혼동하지 않습니다. 일본어 요약에는 반뇌제 인물 명단이 더 길게 제시되지만, 이번에 직접 확인한 2장 1화 전사에서 명시된 **마코토·사츠키** 이외의 사람을 해당 발언에서 단정하지 않습니다.

## 실제 corpus 반영: 11개

- 1장 2화: `canon.gehenna.revitalization_committee.members_and_first_contact` — 위원회 세 사람의 역할과 히나의 직접 대면.
- 1장 6화: `canon.gehenna.revitalization_committee.hina_contact` — **구체적인 구역 계획은 선생에게 전해 들었으며**, 1장 2화의 직접 소개와 모순되지 않게 수정.
- 1장 16~17화: `canon.gehenna.hina.nk_ultra_external_influence` — 외부 조작 중 판단과 회복 후 본래 태도 구분.
- 2장 17·19화: `canon.gehenna.hina.nk_ultra_confronts_prefect_juniors` — 후배 대치와 히나의 구체적 기억.
- 2장 19화: `canon.gehenna.hina.apologizes_to_prefect_juniors_after_nk_ultra` — 공개 사과와 후배들에 대한 직접 사과.
- 2장 19화: `canon.gehenna.hina_makoto.post_nk_ultra_accountability` — **사임 시도와 마코토의 저지**, 이후 책임 인정.
- 2장 21화: `canon.gehenna.hina.post_nk_ultra_reflection_with_sensei` — 히나가 느낀 복합적인 감정과 다시 선택한 방향.
- 2장 1화: `canon.gehenna.raitei.true_nk_ultra_history` — 사츠키 설명 중 히나가 직접 듣지 않은 부분 `audience_only`.
- 2장 18화: `canon.gehenna.shoko.raitei_follower_disclosure` — 쇼코 폭로 중 히나 인지 미확인 부분 `audience_only`.
- 2장 17·19화: `canon.gehenna.hina.prefect_juniors_defend_her_principles` — 후배들이 직접 증언한 히나의 원칙과 신뢰.
- 2장 21화: `canon.gehenna.hina_makoto.storm_behind_nk_ultra` — 히나와 마코토가 직접 파악한 **폭풍** 조직과 외부 위협.

한국어 명칭 **'폭풍'**은 [한국어 위키의 폭풍(블루 아카이브) 항목](https://www.namu.moe/w/%ED%8F%AD%ED%92%8D%28%EB%B8%94%EB%A3%A8%20%EC%95%84%EC%B9%B4%EC%9D%B4%EB%B8%8C%29)에서 일본어 `嵐`·영어 `STORM`과 대조했습니다. 2장 21화의 '이부키는 뇌제의 진정한 유산'이라는 마코토의 발언은 의미가 아직 불분명하므로 혈연·정체·출생 등으로 해석해 확정하지 않습니다. **'폭풍'과 '보덴사츠'의 관련성도 미확정**입니다.

기존 `canon.abydos.hina_makoto.track_shemata`, `canon.gehenna.makoto_vs_hina.pandemonium_institutional_conflict`, `canon.serenade.hina_satsuki.hears_hypnosis_report`는 **유지**합니다. NK 울트라 사건은 별도의 정신조작 사건이고, 오래전 접점/반감의 시점도 분리합니다.

`RAG_MODE=full`에서는 `audience_only` 항목 2개가 히나의 상시 인캐릭터 지식에 포함되지 않습니다. 반대로 히나가 사건 현장에서 겪은 내용은 `self`/`direct_experience`로 남겨 과도하게 방어적으로 무시하지 않도록 합니다.

## 프롬프트 관련 판단 (#514 후속 PR)

- `hina.md`의 책임·힘·권한 사용·일상 태도 분리는 2장 17·19화와 이미 부합합니다. 크게 다시 쓰지 않습니다.
- 2장 21화의 불안과 선생에 대한 신뢰는 관계·감정의 중요한 사례입니다. 다만 **상시 자책·폭력적 본성·어떤 대화든 선생에게 의존** 같은 고정 성격 지침으로 바꾸지 않습니다.
- `world_core.md`는 기본 세계관 전제가 변하지 않으므로 **수정할 근거가 없습니다**.

## 남은 확인 및 운영 작업

- [x] 한국 서버 1·2장 공개 근거 확인 및 일본어 영상의 화별 위치 확보
- [x] 비공식 영어판 게임 대사 전문에서 1장 2·6·16·17화, 2장 1·17·18·19·20·21화의 핵심 장면 확인
- [x] 확인 결과를 `lore.jsonl`에 반영하고 `source.locator`를 장면별로 보강
- [ ] 필요 시 일본어 게임 화면의 각 대사를 프레임별로 대조하여 원어 표현/화자/예외를 재확인 (특히 2장 21화)
- [ ] 한국어판 고유 표현 및 정확한 인명·호칭·장명 대조 (공식 한국어 원문이 제공되면)
- [ ] 2장 21화의 '이부키가 뇌제의 유산'이라는 언급의 정확한 원어와 의미는 **추가 설정이 확인되기 전까지 해석 보류**
- [ ] `hina-lore validate`, 관련 단위 테스트, PR CI 확인
- [ ] 실제 모델에서 서사 질문·평범한 DM 응답·`audience_only` 차단·입력 토큰 변화 관측

**병합 전 판단:** 기록에는 장면별 대사 근거가 생겼지만 비공식 영어 전사의 한계가 있으므로 `crosschecked` 상태를 유지합니다. 빠른 운영 적용이 필요할 때는 PR CI 통과와 수동 근거 검토를 조건으로 하고, 공식 한국어판 문구를 확보한 뒤 추후 보완해도 됩니다. 해당 문서 자체는 LLM에 자동 주입되지 않습니다.
