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
channelElementKey = "channelRenderer"
playlistElementKey = "playlistRenderer"
continuationItemKey = "continuationItemRenderer"
richItemKey = "richItemRenderer"
hashtagElementKey = "hashtagTileRenderer"
hashtagBrowseKey = "FEhashtag"
hashtagVideosPath = ["contents","twoColumnBrowseResultsRenderer","tabs",0,"tabRenderer","content","richGridRenderer","contents"]
hashtagContinuationVideosPath = ["onResponseReceivedActions",0,"appendContinuationItemsAction","continuationItems"]
contentPath = ["contents","twoColumnSearchResultsRenderer","primaryContents","sectionListRenderer","contents"]
fallbackContentPath = ["contents","twoColumnSearchResultsRenderer","primaryContents","richGridRenderer","contents"]
continuationKeyPath = ["continuationItemRenderer","continuationEndpoint","continuationCommand","token"]

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

class ChannelRequestType:
    info = "EgVhYm91dA%3D%3D"
    playlists = "EglwbGF5bGlzdHMYAyABcAA%3D"

CLIENTS = {
    "ANDROID": {"context": {"client": {"clientName": "ANDROID", "clientVersion": "19.09.37"}},"api_key": searchKey},
    "TV_EMBED": {"context": {"client": {"clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER", "clientVersion": "2.0"},"thirdParty": {"embedUrl": "https://www.youtube.com/"}},"api_key": searchKey},
}

def getVideoId(link: str) -> str:
    if "v=" in link: return link.split("v=")[1].split("&")[0][:11]
    if "youtu.be" in link: return link.split("/")[-1].split("?")[0][:11]
    return link[-11:]

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

def getPlaylistId(link: str) -> str:
    m = re.search(r"list=([a-zA-Z0-9-_]+)", link)
    if m:
        pid = m.group(1)
        return "VL" + pid if not pid.startswith("VL") else pid
    return link

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

async def fetch_player(video_id: str, client: str = "ANDROID"):
    url = "https://www.youtube.com/youtubei/v1/player"
    params = {"key": searchKey, "videoId": video_id, "contentCheckOk": True, "racyCheckOk": True}
    data = copy.deepcopy(CLIENTS[client])
    data["videoId"] = video_id
    session = await get_session()
    try:
        async with session.post(url, params=params, json=data) as r:
            if r.status != 200:
                text = await r.text()
                log.error(f"Player fetch failed: {r.status} {text[:500]}")
                return {}
            return await r.json()
    except Exception as e:
        log.error(f"Player exception: {e}")
        return {}
    finally:
        await session.close()

async def fetch_next(video_id: str):
    url = f"https://www.youtube.com/youtubei/v1/next?key={searchKey}"
    data = {"context": {"client": {"clientName": "WEB", "clientVersion": "2.20210224.06.00"}},"videoId": video_id}
    session = await get_session()
    try:
        async with session.post(url, json=data) as r:
            r.raise_for_status()
            return await r.json()
    except:
        return {}
    finally:
        await session.close()

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

async def fetch_browse(browse_id: str, params: str = None, continuation: str = None, hl: str = "en", gl: str = "US"):
    url = "https://www.youtube.com/youtubei/v1/browse"
    payload = copy.deepcopy(requestPayload)
    payload["browseId"] = browse_id
    payload["context"]["client"]["hl"] = hl
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
    except:
        return {}
    finally:
        await session.close()

async def fetch_comments(continuation: str = None, video_id: str = None):
    url = f"https://www.youtube.com/youtubei/v1/next?key={searchKey}"
    data = {"context": {"client": {"clientName": "WEB", "clientVersion": "2.20210224.06.00"}}}
    if continuation:
        data["continuation"] = continuation
    else:
        data["videoId"] = video_id
    session = await get_session()
    try:
        async with session.post(url, json=data) as r:
            r.raise_for_status()
            return await r.json()
    except:
        return {}
    finally:
        await session.close()

async def fetch_suggestions(query: str, hl: str = "en", gl: str = "US"):
    url = "https://clients1.google.com/complete/search"
    params = {"hl": hl, "gl": gl, "q": query, "client": "youtube", "gs_ri": "youtube", "ds": "yt"}
    session = await get_session()
    try:
        async with session.get(url, params=params) as r:
            text = await r.text()
            if not text.startswith("window.google.ac"):
                return []
            json_str = text[text.index("(")+1:text.rindex(")")]
            data = json.loads(json_str)
            return [item[0] for item in data[1]]
    except:
        return []
    finally:
        await session.close()

async def fetch_hashtag_params(hashtag: str, hl: str = "en", gl: str = "US"):
    payload = copy.deepcopy(requestPayload)
    payload["query"] = "#" + hashtag
    payload["context"]["client"]["hl"] = hl
    payload["context"]["client"]["gl"] = gl
    session = await get_session()
    try:
        async with session.post("https://www.youtube.com/youtubei/v1/search", params={"key": searchKey}, json=payload) as r:
            r.raise_for_status()
            data = await r.json()
            items = getValue(data, contentPath) or getValue(data, fallbackContentPath) or []
            for sec in items:
                for item in getValue(sec, ["itemSectionRenderer","contents"]) or []:
                    if hashtagElementKey in item:
                        return getValue(item, [hashtagElementKey, "onTapCommand", "browseEndpoint", "params"])
    except:
        pass
    finally:
        await session.close()
    return None

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
            "likes": getValue(player, ["videoDetails","likes"]),
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

def extract_video_info(player_data: dict):
    vd = player_data.get("videoDetails",{})
    mf = player_data.get("microformat",{}).get("playerMicroformatRenderer",{})
    return {
        "id": vd.get("videoId"),
        "title": vd.get("title"),
        "duration": {"secondsText": vd.get("lengthSeconds")},
        "viewCount": {"text": vd.get("viewCount")},
        "thumbnails": vd.get("thumbnail",{}).get("thumbnails",[]),
        "description": vd.get("shortDescription"),
        "channel": {"name": vd.get("author"), "id": vd.get("channelId")},
        "allowRatings": vd.get("allowRatings"),
        "averageRating": vd.get("averageRating"),
        "keywords": vd.get("keywords"),
        "isLiveContent": vd.get("isLiveContent"),
        "publishDate": mf.get("publishDate"),
        "uploadDate": mf.get("uploadDate"),
        "isFamilySafe": mf.get("isFamilySafe"),
        "category": mf.get("category"),
        "link": f"https://www.youtube.com/watch?v={vd.get('videoId')}",
        "channel_link": f"https://www.youtube.com/channel/{vd.get('channelId')}",
        "isLiveNow": vd.get("isLiveContent") and vd.get("lengthSeconds") == "0"
    }

def extract_formats(player_data: dict):
    return player_data.get("streamingData", {})

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
            elif channelElementKey in item:
                r = item[channelElementKey]
                results.append({
                    "type": "channel",
                    "id": r.get("channelId"),
                    "name": getValue(r, ["title","simpleText"]),
                    "thumbnails": r.get("thumbnail",{}).get("thumbnails",[])
                })
            elif playlistElementKey in item:
                r = item[playlistElementKey]
                results.append({
                    "type": "playlist",
                    "id": r.get("playlistId"),
                    "title": getValue(r, ["title","simpleText"]),
                    "thumbnails": r.get("thumbnail",{}).get("thumbnails",[])
                })
            if len(results) >= limit:
                return results
    return results

def extract_channel_info(data: dict):
    thumbnails = []
    for path in [
        ["header","c4TabbedHeaderRenderer","avatar","thumbnails"],
        ["metadata","channelMetadataRenderer","avatar","thumbnails"],
        ["microformat","microformatDataRenderer","thumbnail","thumbnails"]
    ]:
        t = getValue(data, path)
        if t: thumbnails.extend(t)
    about = getValue(data, ["contents","twoColumnBrowseResultsRenderer","tabs",-1,"tabRenderer","content","sectionListRenderer","contents",0,"itemSectionRenderer","contents",0,"channelAboutFullMetadataRenderer"]) or {}
    return {
        "id": getValue(data, ["metadata","channelMetadataRenderer","externalId"]),
        "title": getValue(data, ["metadata","channelMetadataRenderer","title"]),
        "description": getValue(data, ["metadata","channelMetadataRenderer","description"]),
        "url": getValue(data, ["metadata","channelMetadataRenderer","channelUrl"]),
        "subscribers": getValue(data, ["header","c4TabbedHeaderRenderer","subscriberCountText","simpleText"]),
        "banners": getValue(data, ["header","c4TabbedHeaderRenderer","banner","thumbnails"]),
        "thumbnails": thumbnails,
        "views": getValue(about, ["viewCountText","simpleText"]),
        "joinedDate": getValue(about, ["joinedDateText","runs",-1,"text"]),
        "country": getValue(about, ["country","simpleText"])
    }

def extract_playlist(data: dict):
    sidebar = getValue(data, ["sidebar","playlistSidebarRenderer","items"]) or []
    primary = sidebar[0].get("playlistSidebarPrimaryInfoRenderer", {}) if sidebar else {}
    secondary = sidebar[1].get("playlistSidebarSecondaryInfoRenderer", {}) if len(sidebar)>1 else {}
    owner = getValue(secondary, ["videoOwner","videoOwnerRenderer"]) or {}
    videos_raw = getValue(data, ["contents","twoColumnBrowseResultsRenderer","tabs",0,"tabRenderer","content","sectionListRenderer","contents",0,"itemSectionRenderer","contents",0,"playlistVideoListRenderer","contents"]) or []
    videos = []
    for v in videos_raw:
        r = v.get("playlistVideoRenderer", {})
        if r.get("videoId"):
            videos.append({
                "id": r.get("videoId"),
                "title": getValue(r, ["title","runs",0,"text"]),
                "channel": {
                    "name": getValue(r, ["shortBylineText","runs",0,"text"]),
                    "id": getValue(r, ["shortBylineText","runs",0,"navigationEndpoint","browseEndpoint","browseId"])
                },
                "duration": getValue(r, ["lengthText","simpleText"]),
                "thumbnails": r.get("thumbnail",{}).get("thumbnails",[])
            })
    return {
        "info": {
            "id": getValue(primary, ["title","runs",0,"navigationEndpoint","watchEndpoint","playlistId"]),
            "title": getValue(primary, ["title","runs",0,"text"]),
            "videoCount": getValue(primary, ["stats",0,"runs",0,"text"]),
            "viewCount": getValue(primary, ["stats",1,"simpleText"]),
            "thumbnails": getValue(primary, ["thumbnailRenderer","playlistVideoThumbnailRenderer","thumbnail","thumbnails"]) or getValue(primary, ["thumbnailRenderer","playlistCustomThumbnailRenderer","thumbnail","thumbnails"]),
            "channel": {
                "name": getValue(owner, ["title","runs",0,"text"]),
                "id": getValue(owner, ["title","runs",0,"navigationEndpoint","browseEndpoint","browseId"])
            }
        },
        "videos": videos
    }

def extract_comments(data: dict):
    items = getValue(data, ["onResponseReceivedEndpoints",0,"appendContinuationItemsAction","continuationItems"]) or getValue(data, ["onResponseReceivedEndpoints",0,"reloadContinuationItemsCommand","continuationItems"]) or []
    comments = []
    continuation = None
    for item in items:
        cr = getValue(item, ["commentThreadRenderer","comment","commentRenderer"])
        if cr:
            comments.append({
                "id": cr.get("commentId"),
                "author": {
                    "name": getValue(cr, ["authorText","simpleText"]),
                    "id": getValue(cr, ["authorEndpoint","browseEndpoint","browseId"])
                },
                "content": "".join([run.get("text","") for run in getValue(cr, ["contentText","runs"]) or []]),
                "published": getValue(cr, ["publishedTimeText","runs",0,"text"]),
                "votes": getValue(cr, ["voteCount","simpleText"]),
                "replyCount": cr.get("replyCount")
            })
        elif continuationItemKey in item:
            continuation = getValue(item, continuationKeyPath)
    return {"comments": comments, "continuation": continuation}

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
            "/video/info?video_id={id}": "Full video info + formats",
            "/video/formats?video_id={id}": "Only formats",
            "/video/dl?url={url}": "Download link via Clipto",
            "/search?q={q}&limit=20": "Search",
            "/channel/info?channel_id={id}": "Channel info",
            "/channel/playlists?channel_id={id}": "Playlists",
            "/playlist?playlist_id={id}": "Playlist",
            "/comments?video_id={id}": "Comments",
            "/suggestions?q={q}": "Suggestions",
            "/hashtag?tag={tag}": "Hashtag",
            "/health": "Health"
        }
    }

@app.get("/video/info")
async def video_info(video_id: str = Query(..., min_length=11, max_length=11, regex="^[a-zA-Z0-9_-]+$")):
    start = time.time()
    player = await fetch_player(video_id)
    if not player:
        raise HTTPException(500, "Failed to fetch video data")
    info = extract_video_info(player)
    info.update(extract_formats(player))
    return JSONResponseWithMeta(info, start)

@app.get("/video/formats")
async def video_formats(video_id: str = Query(..., min_length=11, max_length=11, regex="^[a-zA-Z0-9_-]+$")):
    start = time.time()
    player = await fetch_player(video_id, "TV_EMBED")
    return JSONResponseWithMeta(extract_formats(player), start)

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

@app.get("/channel/info")
async def channel_info(channel_id: str = Query(..., min_length=3)):
    start = time.time()
    cid = channel_id if channel_id.startswith("UC") else "UC" + channel_id[2:] if len(channel_id) > 24 else channel_id
    data = await fetch_browse(cid, ChannelRequestType.info)
    if not data:
        raise HTTPException(404, "Channel not found")
    return JSONResponseWithMeta(extract_channel_info(data), start)

@app.get("/channel/playlists")
async def channel_playlists(channel_id: str = Query(..., min_length=3), continuation: Optional[str] = None):
    start = time.time()
    cid = channel_id if channel_id.startswith("UC") else "UC" + channel_id[2:]
    data = await fetch_browse(cid, ChannelRequestType.playlists, continuation)
    items = getValue(data, ["contents","twoColumnBrowseResultsRenderer","tabs",1,"tabRenderer","content","sectionListRenderer","contents",0,"itemSectionRenderer","contents",0,"gridRenderer","items"]) or []
    playlists = []
    for i in items:
        p = i.get("gridPlaylistRenderer", {})
        if p.get("playlistId"):
            playlists.append({
                "id": p["playlistId"],
                "title": getValue(p, ["title","runs",0,"text"]),
                "videoCount": getValue(p, ["videoCountText","simpleText"]),
                "thumbnails": p.get("thumbnail",{}).get("thumbnails",[])
            })
    cont = getValue(data, ["onResponseReceivedActions",0,"appendContinuationItemsAction","continuationItems",-1,"continuationItemRenderer","continuationEndpoint","continuationCommand","token"])
    return JSONResponseWithMeta({"playlists": playlists, "continuation": cont}, start)

@app.get("/playlist")
async def playlist(playlist_id: str = Query(..., min_length=2)):
    start = time.time()
    pid = getPlaylistId(playlist_id)
    data = await fetch_browse(pid)
    return JSONResponseWithMeta(extract_playlist(data), start)

@app.get("/comments")
async def comments(video_id: Optional[str] = Query(None, min_length=11, max_length=11), continuation: Optional[str] = None):
    start = time.time()
    if not video_id and not continuation:
        raise HTTPException(400, "video_id or continuation required")
    data = await fetch_comments(continuation, video_id)
    return JSONResponseWithMeta(extract_comments(data), start)

@app.get("/suggestions")
async def suggestions(q: str = Query(..., min_length=1)):
    start = time.time()
    sugs = await fetch_suggestions(q)
    return JSONResponseWithMeta({"suggestions": sugs}, start)

@app.get("/hashtag")
async def hashtag(tag: str = Query(..., min_length=1), limit: int = 20, continuation: Optional[str] = None):
    start = time.time()
    if continuation:
        data = await fetch_browse(hashtagBrowseKey, continuation=continuation)
        raw = getValue(data, hashtagContinuationVideosPath) or []
    else:
        params = await fetch_hashtag_params(tag)
        if not params:
            raise HTTPException(404, "Hashtag not found")
        data = await fetch_browse(hashtagBrowseKey, params)
        raw = getValue(data, hashtagVideosPath) or []
    videos = []
    for item in raw:
        r = getValue(item, [richItemKey, "content", videoElementKey])
        if r:
            videos.append({
                "id": r.get("videoId"),
                "title": getValue(r, ["title","runs",0,"text"]),
                "channel": getValue(r, ["longBylineText","runs",0,"text"]),
                "duration": getValue(r, ["lengthText","simpleText"]),
                "views": getValue(r, ["viewCountText","simpleText"]),
                "thumbnails": r.get("thumbnail",{}).get("thumbnails",[])
            })
        if len(videos) >= limit:
            break
    cont = getValue(raw, [-1] + continuationKeyPath)
    return JSONResponseWithMeta({"videos": videos, "continuation": cont}, start)

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
