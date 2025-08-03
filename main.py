import os
from typing import Literal, Optional
import requests
from subprocess import Popen
from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import JSONResponse
import uvicorn
from config import Target, TargetCondition, settings, dot
from hashlib import sha256
import hmac
import tarfile
import zipfile
import logging

logger = logging.getLogger()

pwd = os.getcwd()

if settings is None:
    logger.error("Needs config.yml")
    exit(-1)

app = FastAPI(debug=True)


def git_pull(path: str):
    Popen(["git", "pull"], executable=path)
    pass


# https://docs.github.com/ja/webhooks/using-webhooks/validating-webhook-deliveries#python-example
async def verify_signature(req: Request, secret: str) -> bool:
    sig_header = req.headers.get("X-Hub-Signature-256")
    if sig_header is None:
        return False
    hash_str = hmac.new(secret.encode("utf-8"), msg=await req.body(), digestmod=sha256)
    expected_sig = "sha256=" + hash_str.hexdigest()
    return hmac.compare_digest(expected_sig, sig_header)


async def check_ping(req: Request) -> bool:
    req_event = req.headers.get("X-GitHub-Event")
    if req_event is None:
        return False
    if req_event.lower() == "ping":
        return True
    return False


async def check_condition(req: Request, conditions: list[TargetCondition]) -> bool:
    req_body = await req.json()
    req_event = req.headers.get("X-GitHub-Event")
    if req_event is None:
        return False
    for condition in conditions:
        if req_event != condition.eventType:
            continue
        if condition.action is not None:
            if "action" not in req_body:
                continue
            req_action = condition.action
            if req_body["action"] != req_action:
                continue
        return True
    return False


def download_file(meta_uri: str, target_setting: Target, target: str):
    _ZIP_TYPE: list[str] = ["application/zip"]
    _TAR_GZ_TYPE: list[str] = ["application/gzip", "application/tar+gzip"]
    target_file = target_setting.filename
    target_path = target_setting.path
    if target_file is None or target_path is None:
        return False
    target_path = os.path.abspath(target_path)
    token = dot.github_token
    bearer = None
    if token is not None:
        bearer = f"Bearer {token}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if bearer is not None:
        headers["Authorization"] = f"Bearer {token}"
    meta_res = requests.get(meta_uri, headers=headers)
    if meta_res.status_code != 200:
        return False
    meta_body = meta_res.json()
    target_file_url: Optional[str] = None
    target_file_type: Optional[Literal["zip", "gz.tar"]] = None
    if "assets" not in meta_body:
        return False
    for asset in meta_body["assets"]:
        content_type = asset["content_type"]
        if content_type in _ZIP_TYPE:
            target_file_type = "zip"
        elif content_type in _TAR_GZ_TYPE:
            target_file_type = "gz.tar"
        if content_type is None:
            continue
        if asset["name"] == target_file:
            target_file_url = asset["browser_download_url"]
            break
    if target_file_url is None:
        return False
    headers = None
    if bearer is not None:
        headers = {"Authorization": bearer}
    file_res = requests.get(target_file_url, headers=headers, stream=True)
    if file_res.status_code != 200:
        return False
    tmp_filename = f"{target}_{target_file}"
    tmp_dir = os.path.abspath("./tmp/")
    if settings.base is not None and settings.base.tmp is not None:
        tmp_dir = os.path.abspath(settings.base.tmp)
    if not os.path.exists(tmp_dir):
        os.mkdir(tmp_dir)
    tmp_path = f"{tmp_dir}/{tmp_filename}"
    with open(tmp_path, "wb") as f:
        for chunk in file_res.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
    if target_file_type == "zip":
        with zipfile.ZipFile(tmp_path, "r") as f:
            f.extractall(target_path)
    elif target_file_type == "gz.tar":
        with tarfile.TarFile(tmp_filename, "r") as f:
            f.extractall(target_path)
    os.remove(tmp_path)
    return True


@app.post("/update/{target}")
async def hook(target: str, req: Request):
    req_body = await req.json()
    target_setting: Target | None = None
    if target == "this":
        target_setting = settings.this
    elif target in settings.targets:
        target_setting = settings.targets[target]
    if not target_setting:
        logger.info(f"{target}: not found")
        return JSONResponse({"status": "not found"}, 404)
    secret = target_setting.secret
    deploy = target_setting.deploy
    if secret is None:
        secret = ""
    if not verify_signature(req, secret):
        logger.info(f"{target}: Signature error.")
        return JSONResponse({"status": "Signature error."}, 403)
    if check_ping(req):
        return JSONResponse({"status": "ok"}, 200)
    if not check_condition(req, target_setting.conditions):
        logger.debug(f"{target}: not doing.")
        return JSONResponse({"status": "not doing."})
    if deploy == "relation":
        relation = target_setting.relation
        if relation is None or relation == "":
            logger.debug(f"{target}: Please set relation URL.")
            return JSONResponse({"status": "not doing."})
        res = requests.post(url=relation, headers=req.headers, data=await req.body())
        if res.status_code != 200:
            return JSONResponse(res.json(), res.status_code)
    else:
        body = await req.json()
        if "repository" not in body or "full_name" not in body["repository"]:
            logger.info(f"{target}: Not support action type.")
            return JSONResponse({"status": "Not support action type."}, 501)
        if body["repository"]["full_name"] != target_setting.repo:
            logger.info(
                f"{target},{target_setting.repo}: Not match the Target repo and request body."
            )
            return JSONResponse({"status": "not found"}, 404)
        if deploy == "git":
            git_pull(target_setting.repo)
        elif deploy == "download_file":
            if target_setting.filename is None:
                logger.info(f"{target}: Please set filename.")
                return JSONResponse({"status": "Not support action type."}, 501)
            if "release" in req_body:
                if not download_file(
                    f"https://api.github.com/repos/{target_setting.repo}/release/{req_body['release']['id']}",
                    target_setting,
                    target,
                ):
                    return JSONResponse({"status": "failed to download release."}, 500)
            else:
                logger.info(f"{target}: Not support action type.")
                return JSONResponse({"status": "Not support action type."}, 501)
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
