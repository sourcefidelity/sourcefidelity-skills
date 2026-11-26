# R47d — real-batch CJK acceptance

**Status:** measured 27 November 2026 against the 24 November batch. Four papers, ten claims,
three CJK languages (Chinese, Korean, Japanese). Sources were read from their native-script pages;
the English gloss is reviewer-facing and is not evidence. No machine translation is used as evidence.

## Method

- Claims and cited references are taken from the batch `findings.json` and `inventory.json`.
- Each source page was fetched from the cited URL on 27 November 2026 and read in its original
  script. The original quotation is the evidence. The gloss is a short reviewer aid (≤240 characters).
- The native-language retrieval query is recorded per row. It is a search query only, never evidence.
- No judgment was forced to Supports. One claim is recorded as Not Supported because the cited page
  reports different figures.

## Acceptance results

| # | Paper / claim | Lang | Source (URL) | Judgment | Original evidence | English gloss | Retrieval query |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | P240929 C9 — 34 revenue-sharing import slots | zh | 21st Century Economic Report, 2018-04-09 | Supports | “每年的分账大片为34部（20部普通+14部特种分账片）” | “Annual revenue-sharing blockbusters: 34 (20 ordinary + 14 special).” | `中美电影协议 34部 分账大片` |
| 2 | P240929 C11 — 2017 box office ¥559.11bn | zh | National Film Administration, 2018-01-02 | Supports | “2017年全国电影总票房为559.11亿元，同比增长13.45%” | “2017 national box office was ¥55.911 billion, up 13.45%.” | `2017年 全国电影总票房 559.11亿元` |
| 3 | P240929 C13 — second Pirates film not imported | zh | China News Service, 2006-07-13 | Qualified or Mixed | “该片在送审中国电影局时，因为涉及鬼怪内容，而初审不过关，如果终审依然不通过的话，《加勒比海盗Ⅱ》将无缘内地影院。” | “The film failed the first review because of ghost/skeleton content; if final review also fails it will not reach mainland cinemas.” | `加勒比海盗2 初审 鬼怪 未过` |
| 4 | P240929 C14 — third Pirates film cut by 15–20 minutes | zh | CCTV, 2007-05-23 | Supports | “《加勒比海盗3》本周公映 中国内地版删20分钟6月上映” | “Pirates 3 opens this week; the mainland version cuts 20 minutes and opens in June.” | `加勒比海盗3 内地版 删20分钟` |
| 5 | P240920 C7 — Seven Eleven and Lotteria locations become PokéStops/Gyms | ko | Korea JoongAng Daily, 2017-02-24 | Supports | “세븐일레븐의 전국 8500여 개 점포 중 약 7700개는 포켓스톱, 약 800개는 포켓몬고 체육관이 된다. … 롯데리아가 운영하는 7개 브랜드의 2709개 매장도 포켓스톱이나 포켓몬 체육관으로 운영된다.” | “About 7,700 of Seven Eleven’s 8,500 stores become PokéStops and 800 become Gyms; 2,709 Lotteria-brand stores also participate.” | `세븐일레븐 포켓스톱 롯데리아 2709` |
| 6 | P240920 C8 — Seven Eleven +12.7%, Lotteria +7–10% | ko | Korea JoongAng Daily, 2017-02-24 | Not Supported | “커피빈코리아에 따르면 … 매출이 24.5% 늘었다. … 휴대폰 용품 매출이 74.2% 늘었다. … 핫팩 매출도 65.5% 상승했다.” | “The page reports Coffee Bean sales +24.5%, phone accessories +74.2% and hot packs +65.5%; it gives no Seven Eleven 12.7% or Lotteria 7–10% figures.” | `세븐일레븐 매출 12.7% 롯데리아 7-10%` |
| 7 | P240920 C9 — ten official Incheon routes | ko | Incheon Metropolitan City, 2024-07-23 | Qualified or Mixed | “행사 주최기관과 협업하여 지자체 최초로 게임 내 공식 루트 10개를 설치해 …” | “In cooperation with the organisers, the city installed ten official in-game routes, the first by a local government.” | `인천 포켓몬고 공식 루트 10개` |
| 8 | P240920 C13 — Japan safety/property disputes vs Korean security rules | ja | Tanaka, 2018, J-STAGE (DOI 10.34374/inlaw.18.0_35) | Qualified or Mixed | “ポケストップやジムを目当てにプレイヤー … が集まり、土地所有権等への侵害を生じさせることがある。” | “Players gather at PokéStops and Gyms and can infringe land ownership; the abstract does not cover the Korean comparison.” | `AR ポケモンGO 土地所有権 侵害` |
| 9 | P240916 C3 — Return of the King ¥10.32bn in Japan (2004) | ja | Eiren, 2004 box-office table | Supports | “ロード・オブ・ザ・リング　王の帰還 103.2 2月 松竹/Ｈｅ” | “The Return of the King: ¥10.32 billion, February, Shochiku/He.” | `2004年 興行収入 ロード・オブ・ザ・リング 王の帰還` |
| 10 | P241005 C9 — A- CinemaScore, 88% audience, 62% critics | zh | Global Times via GMW, 2026-05-28 | Supports | “目前该片在烂番茄网上收获62%的媒体新鲜度和88%的观众爆米花指数，CinemaScore平台的观众评级也有A-。” | “Rotten Tomatoes: 62% critics and 88% audience; CinemaScore A-.” | `曼达洛人与古古 烂番茄 62% 88% CinemaScore A-` |

## Counts

- Papers: **4** (P240929, P240920, P240916, P241005)
- Claims: **10**
- Languages: **zh, ko, ja** (3)
- Judgments: **Supports 6, Qualified or Mixed 3, Not Supported 1, Not Judged 0**
- Source types: government/regulator, newspaper, city government, academic journal, industry association
- No forced Supports. The Not Supported row is a figure mismatch, not a retrieval failure.
- Every judgment uses the original-script page; the gloss is not evidence.

## Limitations

- The P240920 C13 comparison to South Korea is not in the Tanaka abstract; only the Japan property
  dispute part is supported, so the claim is Qualified or Mixed rather than forced.
- P240929 C13 says the first review failed; the page does not prove the film was never imported, so
  the judgment is Qualified or Mixed.
- Pages were fetched manually on 27 November 2026; three eiga.com pages and Sensor Tower were
  unreachable and were not used.
