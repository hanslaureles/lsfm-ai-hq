from heavy_jobs import run_heavy
import re
import asyncio
import subprocess
from pathlib import Path
from urllib.parse import quote
from bs4 import BeautifulSoup
import aiohttp

URL_REGEX = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+')

def find_url_in_text(text: str) -> str | None:
    """Finds the first HTTP/HTTPS URL inside a string."""
    match = URL_REGEX.search(text)
    if match:
        url = match.group(0)
        return url.rstrip('.,);!?')
    return None

def clean_html_text(html: str) -> str:
    """Strips tags, scripts, and extra whitespace from HTML, keeping headings and lists."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "svg", "noscript", "iframe", "button"]):
        tag.decompose()
    
    # Target main content containers if present
    main_container = soup.find("main") or soup.find("article") or soup.find("div", class_=re.compile(r'content|job|description|posting|body', re.I))
    target = main_container if main_container else soup.body or soup

    text = target.get_text(separator="\n")
    # Normalize spaces and remove excessive empty lines
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)

def scrape_with_headless_edge(url: str) -> str:
    """Fallback renderer using headless Microsoft Edge to bypass Cloudflare challenges."""
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge_bin = next((p for p in edge_paths if Path(p).exists()), None)
    if not edge_bin:
        return ""
    try:
        cmd = [edge_bin, "--headless=new", "--disable-gpu", "--dump-dom", url]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=12, encoding="utf-8", errors="ignore")
        if res.returncode == 0 and res.stdout and len(res.stdout) > 200:
            return clean_html_text(res.stdout)
    except Exception as e:
        print(f"⚠️ Headless Edge scraper error: {e}", flush=True)
    return ""

def is_bot_challenge_page(text: str) -> bool:
    """Checks if the scraped content is a Cloudflare, bot challenge, or login wall page."""
    lower = text.lower()
    return any(phrase in lower for phrase in [
        "performing security verification",
        "security service to protect against malicious bots",
        "cloudflare ray id",
        "please enable javascript",
        "just a moment...",
        "turn on javascript and cookies",
        "verify you are a human",
        "attention required! | cloudflare"
    ])

async def scrape_job_url(url: str) -> dict:
    """
    Fetches raw HTML from a job board or career page using realistic browser headers.
    Falls back to headless Microsoft Edge if Cloudflare or JavaScript protection blocks raw HTTP.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    timeout = aiohttp.ClientTimeout(total=12)
    
    # 1. Primary fast fetch via aiohttp
    try:
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status == 200:
                    html = await response.text(errors="ignore")
                    text = clean_html_text(html)
                    if len(text) > 150 and not is_bot_challenge_page(text):
                        return {
                            "url": url,
                            "content": text[:15000],
                            "success": True,
                            "error": None
                        }
    except Exception as _exc:
        print(f"⚠️ [web_tools.scrape_job_url] suppressed {type(_exc).__name__}: {_exc}", flush=True)

    # 2. Secondary fallback via headless Edge
    loop = asyncio.get_running_loop()
    edge_text = await run_heavy(scrape_with_headless_edge, url)
    if edge_text and len(edge_text) > 150 and not is_bot_challenge_page(edge_text):
        return {
            "url": url,
            "content": edge_text[:15000],
            "success": True,
            "error": None
        }

    return {
        "url": url,
        "content": "",
        "success": False,
        "error": "This webpage (e.g. JobStreet/LinkedIn) is protected by Cloudflare bot verification or login walls. Please copy and paste the job description text directly into Discord!"
    }

def get_portal_search_links(query: str = "ai", location: str = "Philippines") -> dict:
    """
    Generates pre-filtered direct search links for JobStreet, Indeed, and LinkedIn.
    """
    q_enc = quote(query)
    loc_enc = quote(location)
    clean_q = re.sub(r'[^a-zA-Z0-9]', '-', query.lower().strip()).strip('-')

    return {
        "jobstreet": {
            "name": "JobStreet Philippines",
            "url": f"https://ph.jobstreet.com/{clean_q}-jobs",
            "search_url": f"https://ph.jobstreet.com/jobs?keywords={q_enc}&where=Philippines",
            "badge": "💼 JobStreet"
        },
        "indeed": {
            "name": "Indeed Philippines",
            "url": f"https://ph.indeed.com/jobs?q={q_enc}&l={loc_enc}",
            "badge": "🔍 Indeed"
        },
        "linkedin": {
            "name": "LinkedIn Jobs",
            "url": f"https://www.linkedin.com/jobs/search/?keywords={q_enc}&location={loc_enc}",
            "badge": "🌐 LinkedIn"
        }
    }

async def fetch_linkedin_jobs(query: str = "ai", location: str = "Philippines", limit: int = 8) -> list[dict]:
    """
    Scrapes live job postings from LinkedIn's public guest search API.
    Extracts real titles, companies, locations, and direct apply links.
    """
    q_enc = quote(query)
    loc_enc = quote(location)
    li_url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={q_enc}&location={loc_enc}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    timeout = aiohttp.ClientTimeout(total=10)
    jobs = []

    try:
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(li_url) as resp:
                if resp.status == 200:
                    html = await resp.text(errors="ignore")
                    soup = BeautifulSoup(html, "html.parser")
                    cards = soup.find_all("li")
                    for card in cards:
                        t_el = card.find("h3", class_="base-search-card__title")
                        c_el = card.find("h4", class_="base-search-card__subtitle")
                        l_el = card.find("span", class_="job-search-card__location")
                        link_el = card.find("a", class_="base-card__full-link")
                        time_el = card.find("time")

                        if t_el and link_el and link_el.has_attr("href"):
                            raw_url = link_el["href"].split("?")[0]
                            jobs.append({
                                "title": t_el.get_text(strip=True),
                                "company": c_el.get_text(strip=True) if c_el else "Tech Company",
                                "url": raw_url,
                                "tags": [location, "LinkedIn"],
                                "geo": l_el.get_text(strip=True) if l_el else location,
                                "salary": "Competitive",
                                "pub_date": time_el.get_text(strip=True) if time_el else "Recent",
                                "source": "LinkedIn",
                                "description": f"{t_el.get_text(strip=True)} at {c_el.get_text(strip=True) if c_el else ''}"
                            })
                            if len(jobs) >= limit:
                                break
    except Exception as e:
        print(f"⚠️ [LinkedIn Scraper] error: {e}", flush=True)

    return jobs

async def fetch_live_remote_jobs(query: str = "ai", location: str = "Philippines", limit: int = 6) -> list[dict]:
    """
    Fetches real-time tech job listings prioritizing LinkedIn (Philippines & Remote),
    merging with Jobicy and RemoteOK for comprehensive coverage.
    Scores all opportunities against Hans's AI Systems Engineer profile.
    """
    q_lower = query.lower().strip()
    raw_jobs = []

    # 1. Primary: Fetch from LinkedIn (Philippines & Remote)
    target_loc = "Philippines" if any(p in q_lower for p in ["ph", "manila", "philippines", "local"]) else ("Remote" if "remote" in q_lower else location)
    clean_q = re.sub(r'\b(ph|philippines|local|remote)\b', '', q_lower).strip() or "ai"

    li_jobs = await fetch_linkedin_jobs(query=clean_q, location=target_loc, limit=8)
    raw_jobs.extend(li_jobs)

    # If user searched for remote or we need more listings, also fetch LinkedIn Remote
    if len(raw_jobs) < limit or "remote" in q_lower:
        li_remote = await fetch_linkedin_jobs(query=clean_q, location="Remote", limit=5)
        raw_jobs.extend(li_remote)

    # 2. Secondary: Complement with Jobicy & RemoteOK for global remote roles
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=8)

    tag_param = "engineering"
    if any(k in q_lower for k in ["ai", "ml", "python", "llm", "agent"]):
        tag_param = "ai"

    try:
        jobicy_url = f"https://jobicy.com/api/v2/remote-jobs?count=15&tag={tag_param}"
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(jobicy_url) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    for j in data.get("jobs", []):
                        raw_jobs.append({
                            "title": j.get("jobTitle", "Engineer"),
                            "company": j.get("companyName", "Tech Company"),
                            "url": j.get("url", ""),
                            "tags": [j.get("jobCategory", "")] + (j.get("jobIndustry", []) or []),
                            "geo": j.get("jobGeo", "Anywhere (Remote)"),
                            "salary": f"${j.get('annualSalaryMin', '')} - ${j.get('annualSalaryMax', '')}" if j.get('annualSalaryMin') else "Competitive",
                            "pub_date": j.get("pubDate", "")[:10],
                            "source": "Jobicy",
                            "description": clean_html_text(j.get("jobDescription", ""))[:400]
                        })
    except Exception as _exc:
        print(f"⚠️ [web_tools.fetch_live_remote_jobs] suppressed {type(_exc).__name__}: {_exc}", flush=True)

    try:
        ro_tag = "ai" if any(k in q_lower for k in ["ai", "ml", "llm", "agent"]) else "python"
        ro_url = f"https://remoteok.com/api?tag={ro_tag}"
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(ro_url) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    for j in data:
                        if isinstance(j, dict) and j.get("position"):
                            raw_jobs.append({
                                "title": j.get("position", "Software Engineer"),
                                "company": j.get("company", "Remote Tech"),
                                "url": j.get("url", ""),
                                "tags": j.get("tags", []),
                                "geo": j.get("location", "Remote"),
                                "salary": "Competitive",
                                "pub_date": j.get("date", "")[:10] if j.get("date") else "",
                                "source": "RemoteOK",
                                "description": clean_html_text(j.get("description", ""))[:400]
                            })
    except Exception as _exc:
        print(f"⚠️ [web_tools.fetch_live_remote_jobs] suppressed {type(_exc).__name__}: {_exc}", flush=True)

    if not raw_jobs:
        return []

    # 3. Deduplicate and score against Hans's profile
    competencies = [
        "ai", "python", "agent", "llm", "fullstack", "full-stack", "full stack",
        "react", "typescript", "javascript", "developer", "engineer", "software",
        "backend", "frontend", "automation", "api", "ollama", "gpu", "rag"
    ]
    
    unique_jobs = {}
    scored_jobs = []

    for job in raw_jobs:
        url = job.get("url")
        if not url or url in unique_jobs:
            continue
        unique_jobs[url] = True

        title_lower = job["title"].lower()
        desc_lower = job.get("description", "").lower()
        tags_str = " ".join(str(t).lower() for t in job.get("tags", []))
        full_text = f"{title_lower} {tags_str} {desc_lower}"

        # Negative filter: Skip strict VP / Director roles
        if any(bad in title_lower for bad in ["director", "vice president", "vp of", "head of"]):
            continue

        # Score calculation
        score = 68
        matches = []
        for kw in competencies:
            if kw in full_text:
                score += 3
                if kw in title_lower:
                    score += 5
                matches.append(kw)

        # Bonus for query match
        if clean_q and clean_q in full_text:
            score += 10

        # Small bonus for Philippine local roles for Hans's timezone
        if "philippines" in job.get("geo", "").lower() or "manila" in job.get("geo", "").lower():
            score += 4

        score = min(98, score)
        job["score"] = score
        job["matched_keywords"] = list(set(matches))[:5]
        scored_jobs.append(job)

    # Sort descending by score
    scored_jobs.sort(key=lambda x: x["score"], reverse=True)
    return scored_jobs[:limit]


