"""
Web search tool — used by Tier 3 (investigate) messages only.
Searches for permit requirements, supplier stock, code references, pricing.
Uses DuckDuckGo HTML scraping (no API key needed).
"""
import httpx, re
from urllib.parse import quote_plus


async def web_search(query: str, max_results: int = 3) -> str:
    """
    Search the web and return plain-text summaries of top results.
    """
    url = f'https://html.duckduckgo.com/html/?q={quote_plus(query)}'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text

        # Extract result snippets from DDG HTML
        results = []
        # DDG result pattern
        snippet_pattern = re.compile(
            r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL
        )
        title_pattern = re.compile(
            r'class="result__a"[^>]*>(.*?)</a>', re.DOTALL
        )
        snippets = snippet_pattern.findall(html)
        titles = title_pattern.findall(html)

        for i in range(min(max_results, len(snippets))):
            title = re.sub(r'<[^>]+>', '', titles[i]).strip() if i < len(titles) else ''
            snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
            snippet = re.sub(r'\s+', ' ', snippet)
            if title or snippet:
                results.append(f'[{i+1}] {title}\n    {snippet}')

        if not results:
            return f'No results found for: {query}'
        return f'Web search: "{query}"\n\n' + '\n\n'.join(results)

    except Exception as e:
        return f'Search failed for "{query}": {e}'


def web_search_sync(query: str, max_results: int = 3) -> str:
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(web_search(query, max_results))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(web_search(query, max_results))
        loop.close()
        return result
