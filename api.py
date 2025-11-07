import json
import copy
import re
import time
from typing import Any, Optional
import aiohttp
import asyncio
import uvloop
import logging
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse, Response
from contextlib import asynccontextmanager
from collections import OrderedDict
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("SmartYTParser")

searchKey = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"

requestPayload = {
    "context": {
        "client": {
            "clientName": "WEB",
            "clientVersion": "2.20210224.06.00",
            "newVisitorCookie": True,
        },
        "user": {"lockedSafetyMode": False},
    }
}

userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.77 Safari/537.36"

videoElementKey = "videoRenderer"
richItemKey = "richItemRenderer"
contentPath = ["contents","twoColumnSearchResultsRenderer","primaryContents","sectionListRenderer","contents"]
fallbackContentPath = ["contents","twoColumnSearchResultsRenderer","primaryContents","richGridRenderer","contents"]

class SearchMode:
    videos = "EgIQAQ%3D%3D"
    channels = "EgIQAg%3D%3D"
    playlists = "EgIQAw%3D%3D"
    livestreams = "EgJAAQ%3D%3D"

class VideoUploadDateFilter:
    lastHour = "EgQIARAB"
    today = "EgQIAhAB"
    thisWeek = "EgQIAxAB"
    thisMonth = "EgQIBBAB"
    thisYear = "EgQIBRAB"

class VideoDurationFilter:
    short = "EgQQARgB"
    long = "EgQQARgC"

class VideoSortOrder:
    relevance = "CAASAhAB"
    uploadDate = "CAISAhAB"
    viewCount = "CAMSAhAB"
    rating = "CAESAhAB"

def getValue(source: dict, path: list) -> Any:
    val = source
    for p in path:
        if val is None: return None
        if isinstance(p, int):
            val = val[p] if isinstance(val, list) and len(val) > p else None
        else:
            val = val.get(p)
    return val

def parse_duration(duration: str) -> str:
    try:
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if not match:
            return "N/A"
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        formatted = ""
        if hours > 0: formatted += f"{hours}h "
        if minutes > 0: formatted += f"{minutes}m "
        if seconds > 0: formatted += f"{seconds}s"
        return formatted.strip() or "0s"
    except Exception:
        return "N/A"

class JSONResponseWithMeta(JSONResponse):
    def __init__(self, content, start_time, **kwargs):
        time_taken = f"{(time.time() - start_time)*1000:.2f}ms"
        meta = {
            "api_dev": "@ISmartCoder",
            "api_channel": "@abirxdhackz",
            "dev_github": "github.com/abirxdhack",
            "time_taken": time_taken
        }
        content_with_meta = {"meta": meta, "data": content}
        super().__init__(content_with_meta, **kwargs)

async def get_session() -> aiohttp.ClientSession:
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit=100, limit_per_host=20)
    return aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": userAgent}, connector=connector)

async def fetch_search(query: str, params: str = None, continuation: str = None, hc: str = "en", gl: str = "US"):
    url = "https://www.youtube.com/youtubei/v1/search"
    payload = copy.deepcopy(requestPayload)
    payload["query"] = query
    payload["context"]["client"]["hl"] = hc
    payload["context"]["client"]["gl"] = gl
    if params:
        payload["params"] = params
    if continuation:
        payload["continuation"] = continuation
    session = await get_session()
    try:
        async with session.post(url, params={"key": searchKey}, json=payload) as r:
            if r.status != 200:
                return {}
            return await r.json()
    except Exception as e:
        log.error(f"Search fetch error: {e}")
        return {}
    finally:
        await session.close()

async def fetch_player(video_id: str, client: str = "ANDROID"):
    url = "https://www.youtube.com/youtubei/v1/player"
    params = {"key": searchKey, "videoId": video_id, "contentCheckOk": "true", "racyCheckOk": "true"}
    data = copy.deepcopy({"context": {"client": {"clientName": "ANDROID", "clientVersion": "19.09.37"}}})
    session = await get_session()
    try:
        async with session.post(url, params=params, json=data) as r:
            if r.status != 200:
                return {}
            return await r.json()
    except Exception as e:
        log.error(f"Player exception: {e}")
        return {}
    finally:
        await session.close()

async def fetch_youtube_details_api(video_id: str):
    try:
        api_url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails&id={video_id}&key={searchKey}"
        session = await get_session()
        try:
            async with session.get(api_url) as response:
                if response.status != 200:
                    return {"error": "Failed"}
                data = await response.json()
                if not data.get('items'):
                    return {"error": "No video"}
                video = data['items'][0]
                snippet = video['snippet']
                stats = video['statistics']
                content_details = video['contentDetails']
                return {
                    "title": snippet.get('title', 'N/A'),
                    "channel": snippet.get('channelTitle', 'N/A'),
                    "description": snippet.get('description', 'N/A'),
                    "imageUrl": snippet.get('thumbnails', {}).get('high', {}).get('url', ''),
                    "duration": parse_duration(content_details.get('duration', '')),
                    "views": stats.get('viewCount', 'N/A'),
                    "likes": stats.get('likeCount', 'N/A'),
                    "comments": stats.get('commentCount', 'N/A')
                }
        finally:
            await session.close()
    except:
        return {"error": "Failed"}

async def fetch_youtube_details(video_id: str):
    try:
        player = await fetch_player(video_id)
        vd = player.get("videoDetails", {})
        return {
            "title": vd.get("title"),
            "channel": vd.get("author"),
            "description": vd.get("shortDescription"),
            "imageUrl": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            "duration": vd.get("lengthSeconds"),
            "views": vd.get("viewCount"),
            "likes": "N/A",
            "comments": "N/A"
        }
    except:
        return {
            "title": "Unavailable",
            "channel": "N/A",
            "description": "N/A",
            "imageUrl": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            "duration": "N/A",
            "views": "N/A",
            "likes": "N/A",
            "comments": "N/A"
        }

def extract_video_id(url: str) -> Optional[str]:
    patterns = [
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/watch\?v=([^&?\s]+)',
        r'(?:https?:\/\/)?youtu\.be\/([^&?\s]+)',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/embed\/([^&?\s]+)',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/v\/([^&?\s]+)',
        r'(?:https?:\/\/)?(?:www\.)?youtube\.com\/shorts\/([^&?\s]+)'
    ]
    for pattern in patterns:
        match = re.match(pattern, url, re.IGNORECASE)
        if match:
            vid = match.group(1)
            return vid.split('&')[0][:11] if '&' in vid else vid[:11]
    query_match = re.search(r'v=([^&?\s]+)', url)
    if query_match:
        vid = query_match.group(1)
        return vid[:11]
    return None

def extract_search_results(data: dict, limit: int):
    contents = getValue(data, contentPath) or getValue(data, fallbackContentPath) or []
    results = []
    for sec in contents:
        sec_contents = getValue(sec, ["itemSectionRenderer","contents"]) or getValue(sec, ["richSectionRenderer","content","richGridRenderer","contents"]) or []
        for item in sec_contents:
            item = item.get("richItemRenderer", item).get("content", item)
            if videoElementKey in item:
                r = item[videoElementKey]
                results.append({
                    "type": "video",
                    "id": r.get("videoId"),
                    "title": getValue(r, ["title","runs",0,"text"]) or getValue(r, ["title","simpleText"]),
                    "channel": getValue(r, ["longBylineText","runs",0,"text"]) or getValue(r, ["shortBylineText","runs",0,"text"]),
                    "views": getValue(r, ["viewCountText","simpleText"]) or getValue(r, ["viewCountText","runs",0,"text"]),
                    "duration": getValue(r, ["lengthText","simpleText"]) or getValue(r, ["lengthText","accessibility","accessibilityData","label"]),
                    "published": getValue(r, ["publishedTimeText","simpleText"]),
                    "thumbnails": r.get("thumbnail",{}).get("thumbnails",[]) or getValue(r, ["thumbnail","thumbnails"])
                })
            if len(results) >= limit:
                return results
    return results

@asynccontextmanager
async def lifespan(app: FastAPI):
    uvloop.install()
    log.info("SmartYTParser API started with uvloop + aiohttp")
    yield

app = FastAPI(title="SmartYTParser", lifespan=lifespan, docs_url=None, redoc_url=None)

@app.get("/")
async def root():
    return {
        "api": "SmartYTParser",
        "dev": "@ISmartCoder",
        "channel": "@TheSmartDevs",
        "github": "github.com/abirxdhack",
        "endpoints": {
            "/search?q={q}&limit=20": "Search videos",
            "/video/dl?url={url}": "Download link via Clipto"
        }
    }

@app.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    mode: str = Query("videos", regex="^(videos|channels|playlists|livestreams)$"),
    sort: Optional[str] = Query(None, regex="^(uploadDate|viewCount|rating)$"),
    date: Optional[str] = Query(None, regex="^(lastHour|today|thisWeek|thisMonth|thisYear)$"),
    duration: Optional[str] = Query(None, regex="^(short|long)$"),
    continuation: Optional[str] = Query(None)
):
    start = time.time()
    param = getattr(SearchMode, mode, SearchMode.videos)
    if sort == "uploadDate": param += VideoSortOrder.uploadDate
    elif sort == "viewCount": param += VideoSortOrder.viewCount
    elif sort == "rating": param += VideoSortOrder.rating
    if date: param += getattr(VideoUploadDateFilter, date, "")
    if duration: param += getattr(VideoDurationFilter, duration, "")
    data = await fetch_search(q, param if param != SearchMode.videos else None, continuation)
    results = extract_search_results(data, limit)
    cont_paths = [
        ["onResponseReceivedCommands",0,"appendContinuationItemsAction","continuationItems",-1,"continuationItemRenderer","continuationEndpoint","continuationCommand","token"],
        ["onResponseReceivedActions",0,"appendContinuationItemsAction","continuationItems",-1,"continuationItemRenderer","continuationEndpoint","continuationCommand","token"],
        ["onResponseReceivedEndpoints",0,"appendContinuationItemsAction","continuationItems",-1,"continuationItemRenderer","continuationEndpoint","continuationCommand","token"]
    ]
    cont = None
    for path in cont_paths:
        cont = getValue(data, path)
        if cont:
            break
    return JSONResponseWithMeta({"results": results, "continuation": cont}, start)

@app.get("/video/dl")
async def video_dl(url: str = Query(...)):
    start = time.time()
    youtube_url = url.strip()
    if not youtube_url:
        raise HTTPException(400, "Missing 'url' parameter.")
    video_id = extract_video_id(youtube_url)
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL.")
    standard_url = f"https://www.youtube.com/watch?v={video_id}"
    youtube_data = await fetch_youtube_details_api(video_id)
    if "error" in youtube_data:
        youtube_data = await fetch_youtube_details(video_id)
    payload = {"url": standard_url}
    session = await get_session()
    try:
        async with session.post("https://www.clipto.com/api/youtube", json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                ordered = OrderedDict()
                ordered["api_owner"] = "@ISmartCoder"
                ordered["updates_channel"] = "@TheSmartDevs"
                ordered["title"] = data.get("title", youtube_data.get("title", "N/A"))
                ordered["channel"] = youtube_data.get("channel", "N/A")
                ordered["description"] = youtube_data.get("description", "N/A")
                ordered["thumbnail"] = data.get("thumbnail", youtube_data.get("imageUrl"))
                ordered["thumbnail_url"] = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
                ordered["url"] = data.get("url", standard_url)
                ordered["duration"] = youtube_data.get("duration", "N/A")
                ordered["views"] = youtube_data.get("views", "N/A")
                ordered["likes"] = youtube_data.get("likes", "N/A")
                ordered["comments"] = youtube_data.get("comments", "N/A")
                for key, value in data.items():
                    if key not in ordered:
                        ordered[key] = value
                return Response(
                    content=json.dumps(ordered, ensure_ascii=False, indent=4),
                    media_type="application/json"
                )
            else:
                ordered = OrderedDict()
                ordered["api_owner"] = "@ISmartCoder"
                ordered["updates_channel"] = "@TheSmartDevs"
                ordered["title"] = youtube_data.get("title", "N/A")
                ordered["channel"] = youtube_data.get("channel", "N/A")
                ordered["description"] = youtube_data.get("description", "N/A")
                ordered["thumbnail"] = youtube_data.get("imageUrl")
                ordered["thumbnail_url"] = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
                ordered["url"] = standard_url
                ordered["duration"] = youtube_data.get("duration", "N/A")
                ordered["views"] = youtube_data.get("views", "N/A")
                ordered["likes"] = youtube_data.get("likes", "N/A")
                ordered["comments"] = youtube_data.get("comments", "N/A")
                ordered["error"] = "Clipto API failed"
                return Response(
                    content=json.dumps(ordered, ensure_ascii=False, indent=4),
                    media_type="application/json",
                    status_code=500
                )
    except Exception as e:
        log.error(f"Error fetching from Clipto: {e}")
        ordered = OrderedDict()
        ordered["api_owner"] = "@ISmartCoder"
        ordered["updates_channel"] = "@TheSmartDevs"
        ordered["title"] = youtube_data.get("title", "N/A")
        ordered["channel"] = youtube_data.get("channel", "N/A")
        ordered["description"] = youtube_data.get("description", "N/A")
        ordered["thumbnail"] = youtube_data.get("imageUrl")
        ordered["thumbnail_url"] = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        ordered["url"] = standard_url
        ordered["duration"] = youtube_data.get("duration", "N/A")
        ordered["views"] = youtube_data.get("views", "N/A")
        ordered["likes"] = youtube_data.get("likes", "N/A")
        ordered["comments"] = youtube_data.get("comments", "N/A")
        ordered["error"] = "Internal error"
        return Response(
            content=json.dumps(ordered, ensure_ascii=False, indent=4),
            media_type="application/json",
            status_code=500
        )
    finally:
        await session.close()

@app.get("/health")
async def health():
    start = time.time()
    return JSONResponseWithMeta({"status": "ok"}, start)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, workers=1, loop="uvloop")
