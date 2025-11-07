import json
import copy
import re
import time
from typing import Union, List, Dict, Any
from urllib.parse import urlencode
import aiohttp
import asyncio
import uvloop
import logging
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from collections import OrderedDict
import os
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("SmartYTParser")

searchKey = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"

requestPayload = {
    "context": {
        "client": {
            "clientName": "WEB",
            "clientVersion": "2.20210224.06.00",
            "newVisitorCookie": True,
        },
        "user": {
            "lockedSafetyMode": False,
        },
    }
}

userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.77 Safari/537.36"

videoElementKey = "videoRenderer"
channelElementKey = "channelRenderer"
playlistElementKey = "playlistRenderer"
shelfElementKey = "shelfRenderer"
itemSectionKey = "itemSectionRenderer"
continuationItemKey = "continuationItemRenderer"
playerResponseKey = "playerResponse"
richItemKey = "richItemRenderer"
hashtagElementKey = "hashtagTileRenderer"
hashtagBrowseKey = "FEhashtag"
hashtagVideosPath = ["contents","twoColumnBrowseResultsRenderer","tabs",0,"tabRenderer","content","richGridRenderer","contents"]
hashtagContinuationVideosPath = ["onResponseReceivedActions",0,"appendContinuationItemsAction","continuationItems"]
contentPath = ["contents","twoColumnSearchResultsRenderer","primaryContents","sectionListRenderer","contents"]
fallbackContentPath = ["contents","twoColumnSearchResultsRenderer","primaryContents","richGridRenderer","contents"]
continuationContentPath = ["onResponseReceivedCommands",0,"appendContinuationItemsAction","continuationItems"]
continuationKeyPath = ["continuationItemRenderer","continuationEndpoint","continuationCommand","token"]
playlistInfoPath = ["response","sidebar","playlistSidebarRenderer","items"]
playlistVideosPath = ["response","contents","twoColumnBrowseResultsRenderer","tabs",0,"tabRenderer","content","sectionListRenderer","contents",0,"itemSectionRenderer","contents",0,"playlistVideoListRenderer","contents"]
playlistPrimaryInfoKey = "playlistSidebarPrimaryInfoRenderer"
playlistSecondaryInfoKey = "playlistSidebarSecondaryInfoRenderer"
playlistVideoKey = "playlistVideoRenderer"

class ResultMode:
    json = 0
    dict = 1

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
    "MWEB": {"context": {"client": {"clientName": "MWEB", "clientVersion": "2.20211109.01.00"}},"api_key": searchKey},
    "ANDROID": {"context": {"client": {"clientName": "ANDROID", "clientVersion": "19.09.37"}},"api_key": searchKey},
    "TV_EMBED": {"context": {"client": {"clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER", "clientVersion": "2.0"},"thirdParty": {"embedUrl": "https://www.youtube.com/"}},"api_key": searchKey},
}

def getVideoId(link: str) -> str:
    if "v=" in link: return link.split("v=")[1].split("&")[0][:11]
    if "youtu.be" in link: return link.split("/")[-1].split("?")[0][:11]
    return link[-11:]

def getPlaylistId(link: str) -> str:
    m = re.search(r"list=([a-zA-Z0-9-_]+)", link)
    return "VL" + m.group(1) if m and not m.group(1).startswith("VL") else m.group(1)

def getValue(source: dict, path: list) -> Any:
    val = source
    for p in path:
        if val is None: return None
        if isinstance(p, int): val = val[p] if len(val) > p else None
        else: val = val.get(p)
    return val

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

async def aiohttp_session():
    timeout = aiohttp.ClientTimeout(total=30)
    return aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": userAgent})

async def fetch_player(video_id: str, client: str = "ANDROID"):
    url = "https://www.youtube.com/youtubei/v1/player"
    params = {"key": searchKey, "videoId": video_id, "contentCheckOk": True, "racyCheckOk": True}
    data = copy.deepcopy(CLIENTS[client])
    async with aiohttp_session() as session:
        async with session.post(url, params=params, json=data) as r:
            r.raise_for_status()
            return await r.json()

async def fetch_next(video_id: str):
    url = f"https://www.youtube.com/youtubei/v1/next?key={searchKey}"
    data = {"context": {"client": {"clientName": "WEB", "clientVersion": "2.20210224.06.00"}},"videoId": video_id}
    async with aiohttp_session() as session:
        async with session.post(url, json=data) as r:
            r.raise_for_status()
            return await r.json()

async def fetch_search(query: str, params: str = None, continuation: str = None, hl: str = "en", gl: str = "US"):
    url = "https://www.youtube.com/youtubei/v1/search"
    payload = copy.deepcopy(requestPayload)
    payload["query"] = query
    payload["context"]["client"]["hl"] = hl
    payload["context"]["client"]["gl"] = gl
    if params: payload["params"] = params
    if continuation: payload["continuation"] = continuation
    async with aiohttp_session() as session:
        async with session.post(url, params={"key": searchKey}, json=payload) as r:
            r.raise_for_status()
            return await r.json()

async def fetch_browse(browse_id: str, params: str = None, continuation: str = None, hl: str = "en", gl: str = "US"):
    url = "https://www.youtube.com/youtubei/v1/browse"
    payload = copy.deepcopy(requestPayload)
    payload["browseId"] = browse_id
    payload["context"]["client"]["hl"] = hl
    payload["context"]["client"]["gl"] = gl
    if params: payload["params"] = params
    if continuation: payload["continuation"] = continuation
    async with aiohttp_session() as session:
        async with session.post(url, params={"key": searchKey}, json=payload) as r:
            r.raise_for_status()
            return await r.json()

async def fetch_comments(continuation: str = None, video_id: str = None):
    url = f"https://www.youtube.com/youtubei/v1/next?key={searchKey}"
    data = {"context": {"client": {"clientName": "WEB", "clientVersion": "2.20210224.06.00"}}}
    if continuation:
        data["continuation"] = continuation
    else:
        data["videoId"] = video_id
    async with aiohttp_session() as session:
        async with session.post(url, json=data) as r:
            r.raise_for_status()
            return await r.json()

async def fetch_suggestions(query: str, hl: str = "en", gl: str = "US"):
    url = "https://clients1.google.com/complete/search"
    params = {"hl": hl, "gl": gl, "q": query, "client": "youtube", "gs_ri": "youtube", "ds": "yt"}
    async with aiohttp_session() as session:
        async with session.get(url, params=params) as r:
            text = await r.text()
            json_str = text[text.index("(")+1:text.rindex(")")]
            data = json.loads(json_str)
            return [item[0] for item in data[1]]

async def fetch_hashtag_params(hashtag: str, hl: str = "en", gl: str = "US"):
    payload = copy.deepcopy(requestPayload)
    payload["query"] = "#" + hashtag
    payload["context"]["client"]["hl"] = hl
    payload["context"]["client"]["gl"] = gl
    async with aiohttp_session() as session:
        async with session.post("https://www.youtube.com/youtubei/v1/search", params={"key": searchKey}, json=payload) as r:
            r.raise_for_status()
            data = await r.json()
            items = getValue(data, contentPath) or getValue(data, fallbackContentPath) or []
            for sec in items:
                for item in getValue(sec, ["itemSectionRenderer","contents"]) or []:
                    if hashtagElementKey in item:
                        return getValue(item, [hashtagElementKey, "onTapCommand", "browseEndpoint", "params"])
    return None

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
            "comments": getValue(player, ["commentsCount"])
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
        for item in getValue(sec, ["itemSectionRenderer","contents"]) or []:
            if videoElementKey in item:
                r = item[videoElementKey]
                results.append({
                    "type": "video",
                    "id": r.get("videoId"),
                    "title": getValue(r, ["title","runs",0,"text"]),
                    "channel": getValue(r, ["longBylineText","runs",0,"text"]),
                    "views": getValue(r, ["viewCountText","simpleText"]),
                    "duration": getValue(r, ["lengthText","simpleText"]),
                    "published": getValue(r, ["publishedTimeText","simpleText"]),
                    "thumbnails": r.get("thumbnail",{}).get("thumbnails",[])
                })
            elif channelElementKey in item:
                r = item[channelElementKey]
                results.append({"type":"channel","id":r.get("channelId"),"name":getValue(r,["title","simpleText"]),"thumbnails":r.get("thumbnail",{}).get("thumbnails",[])})
            elif playlistElementKey in item:
                r = item[playlistElementKey]
                results.append({"type":"playlist","id":r.get("playlistId"),"title":getValue(r,["title","simpleText"]),"thumbnails":r.get("thumbnail",{}).get("thumbnails",[])})
            if len(results) >= limit: return results
    return results

def extract_channel_info(data: dict):
    thumbnails = []
    for path in [["header","c4TabbedHeaderRenderer","avatar","thumbnails"],
                 ["metadata","channelMetadataRenderer","avatar","thumbnails"],
                 ["microformat","microformatDataRenderer","thumbnail","thumbnails"]]:
        t = getValue(data, path)
        if t: thumbnails.extend(t)
    playlists = []
    items = getValue(data, ["contents","twoColumnBrowseResultsRenderer","tabs",3,"tabRenderer","content","sectionListRenderer","contents",0,"itemSectionRenderer","contents",0,"gridRenderer","items"]) or []
    for i in items:
        p = i.get("gridPlaylistRenderer")
        if p:
            playlists.append({
                "id": p.get("playlistId"),
                "title": getValue(p, ["title","runs",0,"text"]),
                "videoCount": getValue(p, ["videoCountShortText","simpleText"]),
                "thumbnails": p.get("thumbnail",{}).get("thumbnails",[])
            })
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
        "country": getValue(about, ["country","simpleText"]),
        "playlists": playlists
    }

def extract_playlist(data: dict):
    sidebar = getValue(data, ["sidebar","playlistSidebarRenderer","items"]) or []
    primary = sidebar[0].get(playlistPrimaryInfoKey, {}) if sidebar else {}
    secondary = sidebar[1].get(playlistSecondaryInfoKey, {}) if len(sidebar)>1 else {}
    owner = getValue(secondary, ["videoOwner","videoOwnerRenderer"]) or {}
    videos_raw = getValue(data, ["contents","twoColumnBrowseResultsRenderer","tabs",0,"tabRenderer","content","sectionListRenderer","contents",0,"itemSectionRenderer","contents",0,"playlistVideoListRenderer","contents"]) or []
    videos = []
    for v in videos_raw:
        r = v.get(playlistVideoKey, {})
        if r.get("videoId"):
            videos.append({
                "id": r.get("videoId"),
                "title": getValue(r, ["title","runs",0,"text"]),
                "channel": {"name": getValue(r, ["shortBylineText","runs",0,"text"]),"id": getValue(r, ["shortBylineText","runs",0,"navigationEndpoint","browseEndpoint","browseId"])},
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
            "channel": {"name": getValue(owner, ["title","runs",0,"text"]),"id876": getValue(owner, ["title","runs",0,"navigationEndpoint","browseEndpoint","browseId"])}
        },
        "videos": videos
    }

def extract_comments(data: dict):
    items = getValue(data, ["onResponseReceivedEndpoints",0,"appendContinuationItemsAction","continuationItems"]) or getValue(data, ["onResponseReceivedEndpoints",1,"reloadContinuationItemsCommand","continuationItems"]) or []
    comments = []
    continuation = None
    for item in items:
        cr = getValue(item, ["commentThreadRenderer","comment","commentRenderer"])
        if cr:
            comments.append({
                "id": cr.get("commentId"),
                "author": {"name": getValue(cr, ["authorText","simpleText"]),"id": getValue(cr, ["authorEndpoint","browseEndpoint","browseId"])},
                "content": "".join([run["text"] for run in getValue(cr, ["contentText","runs"]) or []]),
                "published": getValue(cr, ["publishedTimeText","runs",0,"text"]),
                "votes": getValue(cr, ["voteCount","simpleText"]),
                "replyCount": cr.get("replyCount")
            })
        elif continuationItemKey in item:
            continuation = getValue(item, continuationKeyPath)
    return {"comments": comments, "continuation": continuation}

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    log.info("SmartYTParser API started with uvloop + aiohttp")
    yield

app = FastAPI(title="SmartYTParser", lifespan=lifespan, docs_url=None, redoc_url=None)

@app.get("/")
async def root():
    return {
        "api": "SmartYTParser",
        "dev": "@ISmartCoder",
        "channel": "@abirxdhackz",
        "github": "github.com/abirxdhack",
        "endpoints": {
            "/video/info?video_id={id}": "Get full video details + formats",
            "/video/formats?video_id={id}": "Get only streaming formats",
            "/video/dl?url={youtube_url}": "Get direct download link via Clipto",
            "/search?q={query}&limit=20&mode=videos&sort=uploadDate&date=today&duration=short&continuation=&hl=en&gl=US": "Search videos/channels/playlists",
            "/channel/info?channel_id={id_or_@handle}": "Channel metadata + about",
            "/channel/playlists?channel_id={id}&continuation=": "Channel playlists with pagination",
            "/playlist?playlist_id={id}": "Full playlist info + all videos",
            "/comments?video_id={id}&continuation=": "Video comments with replies count",
            "/suggestions?q={query}": "YouTube search suggestions",
            "/hashtag?tag={tag}&limit=20&continuation=": "Hashtag videos",
            "/health": "Health check"
        },
        "example": "https://your-app.herokuapp.com/video/dl?url=https://youtu.be/dQw4w9WgXcQ"
    }

@app.get("/video/info")
async def video_info(video_id: str = Query(..., regex="^.{11}$")):
    start = time.time()
    log.info(f"Fetching video info: {video_id}")
    player = await fetch_player(video_id)
    info = extract_video_info(player)
    info.update(extract_formats(player))
    return JSONResponseWithMeta(info, start)

@app.get("/video/formats")
async def video_formats(video_id: str = Query(..., regex="^.{11}$")):
    start = time.time()
    log.info(f"Fetching formats: {video_id}")
    player = await fetch_player(video_id, "TV_EMBED")
    return JSONResponseWithMeta(extract_formats(player), start)

@app.get("/search")
async def search(q: str, limit: int = 20, mode: str = "videos", sort: str = None, date: str = None, duration: str = None, continuation: str = None, hl: str = "en", gl: str = "US"):
    start = time.time()
    log.info(f"Search: {q} | mode: {mode} | limit: {limit}")
    param = getattr(SearchMode, mode, SearchMode.videos)
    if sort == "uploadDate": param += VideoSortOrder.uploadDate
    if sort == "viewCount": param += VideoSortOrder.viewCount
    if sort == "rating": param += VideoSortOrder.rating
    if date: param += getattr(VideoUploadDateFilter, date, "")
    if duration: param += getattr(VideoDurationFilter, duration, "")
    data = await fetch_search(q, param if param != SearchMode.videos else None, continuation, hl, gl)
    results = extract_search_results(data, limit)
    cont = getValue(data, ["onResponseReceivedCommands",0,"appendContinuationItemsAction","continuationItems",-1,"continuationItemRenderer","continuationEndpoint","continuationCommand","token"])
    return JSONResponseWithMeta({"results": results, "continuation": cont}, start)

@app.get("/channel/info")
async def channel_info(channel_id: str, hl: str = "en", gl: str = "US"):
    start = time.time()
    if "@" in channel_id: channel_id = channel_id.replace("@","")
    cid = channel_id if channel_id.startswith("UC") else "UC" + channel_id[2:] if len(channel_id)>2 else channel_id
    log.info(f"Channel info: {cid}")
    data = await fetch_browse(cid, ChannelRequestType.info, hl=hl, gl=gl)
    return JSONResponseWithMeta(extract_channel_info(data), start)

@app.get("/channel/playlists")
async def channel_playlists(channel_id: str, continuation: str = None, hl: str = "en", gl: str = "US"):
    start = time.time()
    if "@" in channel_id: channel_id = channel_id.replace("@","")
    cid = channel_id if channel_id.startswith("UC") else "UC" + channel_id[2:]
    log.info(f"Channel playlists: {cid}")
    data = await fetch_browse(cid, ChannelRequestType.playlists, continuation, hl, gl)
    items = getValue(data, ["contents","twoColumnBrowseResultsRenderer","tabs",1,"tabRenderer","content","sectionListRenderer","contents",0,"itemSectionRenderer","contents",0,"gridRenderer","items"]) or []
    playlists = []
    for i in items:
        p = i.get("gridPlaylistRenderer",{})
        if p.get("playlistId"):
            playlists.append({"id":p["playlistId"],"title":getValue(p,["title","runs",0,"text"]),"videoCount":getValue(p,["videoCountText","simpleText"]),"thumbnails":p.get("thumbnail",{}).get("thumbnails",[])})
    cont = getValue(data, ["onResponseReceivedActions",0,"appendContinuationItemsAction","continuationItems",-1,"continuationItemRenderer","continuationEndpoint","continuationCommand","token"])
    return JSONResponseWithMeta({"playlists": playlists, "continuation": cont}, start)

@app.get("/playlist")
async def playlist(playlist_id: str, hl: str = "en", gl: str = "US"):
    start = time.time()
    pid = getPlaylistId(playlist_id)
    log.info(f"Playlist: {pid}")
    data = await fetch_browse(pid, hl=hl, gl=gl)
    return JSONResponseWithMeta(extract_playlist(data), start)

@app.get("/comments")
async def comments(video_id: str = None, continuation: str = None):
    start = time.time()
    log.info(f"Comments: {video_id or continuation}")
    data = await fetch_comments(continuation, video_id)
    return JSONResponseWithMeta(extract_comments(data), start)

@app.get("/suggestions")
async def suggestions(q: str, hl: str = "en", gl: str = "US"):
    start = time.time()
    log.info(f"Suggestions: {q}")
    sugs = await fetch_suggestions(q, hl, gl)
    return JSONResponseWithMeta({"suggestions": sugs}, start)

@app.get("/hashtag")
async def hashtag(tag: str, limit: int = 20, continuation: str = None, hl: str = "en", gl: str = "US"):
    start = time.time()
    log.info(f"Hashtag: #{tag}")
    if continuation:
        data = await fetch_browse(hashtagBrowseKey, continuation=continuation, hl=hl, gl=gl)
        raw = getValue(data, hashtagContinuationVideosPath) or []
    else:
        params = await fetch_hashtag_params(tag, hl, gl)
        if not params: raise HTTPException(status_code=404, detail="Hashtag not found")
        data = await fetch_browse(hashtagBrowseKey, params, hl=hl, gl=gl)
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
        if len(videos) >= limit: break
    cont = getValue(raw, [-1] + continuationKeyPath)
    return JSONResponseWithMeta({"videos": videos, "continuation": cont}, start)

@app.get("/video/dl")
async def video_dl(url: str = Query(..., alias="url")):
    start = time.time()
    youtube_url = url.strip()
    if not youtube_url:
        raise HTTPException(status_code=400, detail="Missing 'url' parameter.")
    video_id = getVideoId(youtube_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL.")
    standard_url = f"https://www.youtube.com/watch?v={video_id}"
    youtube_data = await fetch_youtube_details(video_id)
    payload = {"url": standard_url}
    try:
        async with aiohttp_session() as session:
            async with session.post("https://www.clipto.com/api/youtube", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ordered = OrderedDict()
                    ordered["api_owner"] = "@ISmartCoder"
                    ordered["updates_channel"] = "@TheSmartDevs"
                    ordered["title"] = data.get("title", youtube_data["title"])
                    ordered["channel"] = youtube_data["channel"]
                    ordered["description"] = youtube_data["description"]
                    ordered["thumbnail"] = data.get("thumbnail", youtube_data["imageUrl"])
                    ordered["thumbnail_url"] = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
                    ordered["url"] = data.get("url", standard_url)
                    ordered["duration"] = youtube_data["duration"]
                    ordered["views"] = youtube_data["views"]
                    ordered["likes"] = youtube_data["likes"]
                    ordered["comments"] = youtube_data["comments"]
                    for key, value in data.items():
                        if key not in ordered:
                            ordered[key] = value
                    return JSONResponseWithMeta(dict(ordered), start)
                else:
                    ordered = OrderedDict()
                    ordered["api_owner"] = "@ISmartCoder"
                    ordered["updates_channel"] = "@TheSmartDevs"
                    ordered["title"] = youtube_data["title"]
                    ordered["channel"] = youtube_data["channel"]
                    ordered["description"] = youtube_data["description"]
                    ordered["thumbnail"] = youtube_data["imageUrl"]
                    ordered["thumbnail_url"] = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
                    ordered["url"] = standard_url
                    ordered["duration"] = youtube_data["duration"]
                    ordered["views"] = youtube_data["views"]
                    ordered["likes"] = youtube_data["likes"]
                    ordered["comments"] = youtube_data["comments"]
                    ordered["error"] = "Failed to fetch download URL from Clipto API."
                    return JSONResponseWithMeta(dict(ordered), start, status_code=500)
    except:
        ordered = OrderedDict()
        ordered["api_owner"] = "@ISmartCoder"
        ordered["updates_channel"] = "@TheSmartDevs"
        ordered["title"] = youtube_data["title"]
        ordered["channel"] = youtube_data["channel"]
        ordered["description"] = youtube_data["description"]
        ordered["thumbnail"] = youtube_data["imageUrl"]
        ordered["thumbnail_url"] = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        ordered["url"] = standard_url
        ordered["duration"] = youtube_data["duration"]
        ordered["views"] = youtube_data["views"]
        ordered["likes"] = youtube_data["likes"]
        ordered["comments"] = youtube_data["comments"]
        ordered["error"] = "Something went wrong. Please contact @ISmartCoder and report the bug."
        return JSONResponseWithMeta(dict(ordered), start, status_code=500)

@app.get("/health")
async def health():
    start = time.time()
    return JSONResponseWithMeta({"status": "ok"}, start)

port = int(os.environ.get("PORT", 8000))
if __name__ == "__main__":
    uvloop.install()
    uvicorn.run("main:app", host="0.0.0.0", port=port, workers=1, loop="uvloop")