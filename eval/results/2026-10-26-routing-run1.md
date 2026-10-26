# Ciel routing eval — 2026-10-26

- Planner: live LLM router · HQ `20ea5ec` · pause 25 s · cases: 50 (10 Japanese)
- **route accuracy: 1.000** (Japanese: 1.000) · exact: 0.960
- Planner source per case: {"fallback": 2, "keyword": 1, "llm": 47} ("fallback" = the router call failed and the keyword router decided)
- Model that decided each case: {"(none)": 3, "openai/gpt-oss-120b": 5, "qwen/qwen3.8-27b": 42} ("(none)" = a keyword fast path, the keyword router, or an error)

## Every case

| Prompt | Lang | Expected | Got | Route | Exact | Source | Model |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| check my PC vitals | en | eunchae.vitals | eunchae.vitals | yes | yes | llm | qwen/qwen3.8-27b |
| is my PC struggling right now? | en | eunchae.vitals | eunchae.vitals | yes | yes | llm | openai/gpt-oss-120b |
| run a syntax check on all the HQ scripts | en | eunchae.compile_all | eunchae.compile_all | yes | yes | llm | qwen/qwen3.8-27b |
| test git_sentinel.py | en | eunchae.test | eunchae.test | yes | yes | llm | qwen/qwen3.8-27b |
| what's the git status of HQ? | en | kazuha.git_status | kazuha.git_status | yes | yes | llm | qwen/qwen3.8-27b |
| review my uncommitted changes for problems | en | kazuha.review | kazuha.git_status, kazuha.review | yes | no | llm | qwen/qwen3.8-27b |
| what does llm_client do when Groq fails? | en | kazuha.ask | kazuha.ask | yes | yes | llm | qwen/qwen3.8-27b |
| audit my portfolio site | en | yunjin.audit | yunjin.audit | yes | yes | llm | qwen/qwen3.8-27b |
| are there broken images on my portfolio? | en | yunjin.audit | yunjin.audit | yes | yes | llm | qwen/qwen3.8-27b |
| any new emails from recruiters? | en | sakura.inbox | sakura.inbox | yes | yes | llm | qwen/qwen3.8-27b |
| check my inbox | en | sakura.inbox | sakura.inbox | yes | yes | llm | qwen/qwen3.8-27b |
| what's on my plate today? | en | sakura.priorities | sakura.priorities | yes | yes | llm | qwen/qwen3.8-27b |
| find my notes about the WHEA crash | en | sakura.vault_search | sakura.vault_search | yes | yes | llm | qwen/qwen3.8-27b |
| clean up the promo emails in my inbox | en | sakura.clean | sakura.clean | yes | yes | llm | qwen/qwen3.8-27b |
| do a full mass cleanse of my inbox | en | sakura.cleanse | sakura.cleanse | yes | yes | llm | qwen/qwen3.8-27b |
| rebuild my resume PDF | en | chaewon.rebuild_resume | chaewon.rebuild_resume | yes | yes | llm | qwen/qwen3.8-27b |
| find remote applied AI engineer jobs | en | chaewon.scout | chaewon.scout | yes | yes | llm | qwen/qwen3.8-27b |
| what's the weather in Cavite? | en | ciel.weather | ciel.weather | yes | yes | llm | qwen/qwen3.8-27b |
| search the web for the latest Python 3.14 release notes | en | ciel.web_search | ciel.web_search | yes | yes | llm | qwen/qwen3.8-27b |
| explain the difference between TCP and UDP | en | ciel.direct | ciel.direct | yes | yes | llm | qwen/qwen3.8-27b |
| write a binary search in Python | en | ciel.direct | ciel.direct | yes | yes | fallback | (none) |
| what are my learned rules? | en | ciel.read_rules | ciel.read_rules | yes | yes | llm | qwen/qwen3.8-27b |
| who am I? read my profile | en | ciel.read_profile | ciel.read_profile | yes | yes | llm | qwen/qwen3.8-27b |
| what are my working preferences? | en | ciel.read_preferences | ciel.read_preferences | yes | yes | llm | qwen/qwen3.8-27b |
| clear memory | en | ciel.reset | ciel.reset | yes | yes | keyword | (none) |
| any suggestions on what I should do next? | en | ciel.appraisal | ciel.appraisal | yes | yes | llm | qwen/qwen3.8-27b |
| check my inbox and my PC vitals | en | eunchae.vitals, sakura.inbox | eunchae.vitals, sakura.inbox | yes | yes | llm | qwen/qwen3.8-27b |
| tell me more about those risks | en | kazuha.git_status | kazuha.review | yes | no | llm | qwen/qwen3.8-27b |
| run that again | en | eunchae.vitals | eunchae.vitals | yes | yes | llm | qwen/qwen3.8-27b |
| save that to my daily notes | en | ciel.save_to_daily | ciel.save_to_daily | yes | yes | llm | qwen/qwen3.8-27b |
| PCの状態を確認して | ja | eunchae.vitals | eunchae.vitals | yes | yes | llm | qwen/qwen3.8-27b |
| 受信トレイに新しいメールある？ | ja | sakura.inbox | sakura.inbox | yes | yes | llm | qwen/qwen3.8-27b |
| ポートフォリオを監査して | ja | yunjin.audit | yunjin.audit | yes | yes | llm | qwen/qwen3.8-27b |
| 履歴書のPDFを作り直して | ja | chaewon.rebuild_resume | chaewon.rebuild_resume | yes | yes | llm | qwen/qwen3.8-27b |
| gitの状態を教えて | ja | kazuha.git_status | kazuha.git_status | yes | yes | llm | qwen/qwen3.8-27b |
| 東京の天気は？ | ja | ciel.weather | ciel.weather | yes | yes | llm | qwen/qwen3.8-27b |
| TCPとUDPの違いを説明して | ja | ciel.direct | ciel.direct | yes | yes | llm | qwen/qwen3.8-27b |
| 今日やることは何？ | ja | sakura.priorities | sakura.priorities | yes | yes | llm | qwen/qwen3.8-27b |
| リモートのAIエンジニア求人を探して | ja | chaewon.scout | chaewon.scout | yes | yes | llm | qwen/qwen3.8-27b |
| もう一度確認して | ja | sakura.inbox | sakura.inbox | yes | yes | llm | qwen/qwen3.8-27b |
| What's Next.js 16 changing? | en | ciel.web_search | ciel.web_search | yes | yes | llm | qwen/qwen3.8-27b |
| How do I clear context in a React reducer? | en | ciel.direct | ciel.direct | yes | yes | fallback | (none) |
| How does temperature work in LLM sampling? | en | ciel.direct | ciel.direct | yes | yes | llm | qwen/qwen3.8-27b |
| Search my inbox for recruiter emails | en | sakura.inbox | sakura.inbox | yes | yes | llm | qwen/qwen3.8-27b |
| Weather in Silang, Cavite | en | ciel.weather | ciel.weather | yes | yes | llm | qwen/qwen3.8-27b |
| Any suggestions for naming this variable? | en | ciel.direct | ciel.direct | yes | yes | llm | qwen/qwen3.8-27b |
| any suggestions for my workspace right now? | en | ciel.appraisal | ciel.appraisal | yes | yes | llm | openai/gpt-oss-120b |
| what's the news on AI regulation? | en | ciel.news | ciel.news | yes | yes | llm | openai/gpt-oss-120b |
| any headlines about the Philippine peso today? | en | ciel.news | ciel.news | yes | yes | llm | openai/gpt-oss-120b |
| is it going to rain in my area this afternoon? | en | ciel.weather | ciel.weather | yes | yes | llm | openai/gpt-oss-120b |
