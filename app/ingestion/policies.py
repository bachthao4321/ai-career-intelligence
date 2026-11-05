"""Conservative handling of personal-research sources without redistribution permission."""

from urllib.parse import urlsplit

LOCAL_ONLY_SOURCES = ("itviec", "topcv")
LOCAL_ONLY_URL_PATTERN = r"^https?://([^/?#]*@)?([^/?#]*\.)?(itviec\.com|topcv\.vn)(:\d+)?(/|$)"


def local_research_only(source: str, url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return source in LOCAL_ONLY_SOURCES or any(
        host == domain or host.endswith("." + domain) for domain in ("itviec.com", "topcv.vn")
    )
