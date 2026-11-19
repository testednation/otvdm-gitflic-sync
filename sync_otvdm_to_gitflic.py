#!/usr/bin/env python3
"""
Sync the latest recommended AppVeyor CI build of otya128/winevdm (otvdm)
to a GitFlic project as a tagged Release with the build .zip attached.
"""

import os
import sys
import tempfile
from pathlib import Path
import requests

APPVEYOR_ACCOUNT = "otya128"
APPVEYOR_PROJECT = "winevdm"
APPVEYOR_BRANCH = "master"
RECOMMENDED_JOB_MARKER = "THIS_BUILD_IS_RECOMMENDED__VCXPROJ_BUILD=1"

GITFLIC_API_BASE = os.environ.get("GITFLIC_API_BASE", "https://api.gitflic.ru").rstrip("/")
GITFLIC_OWNER = os.environ.get("GITFLIC_OWNER", "starware")
GITFLIC_PROJECT = os.environ.get("GITFLIC_PROJECT", "otvdm")
GITFLIC_TOKEN = os.environ.get("GITFLIC_API_TOKEN", "")


def get_latest_appveyor_build() -> dict:
    url = f"https://ci.appveyor.com/api/projects/{APPVEYOR_ACCOUNT}/{APPVEYOR_PROJECT}/branch/{APPVEYOR_BRANCH}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()["build"]


def pick_recommended_job(build: dict) -> dict:
    jobs = build.get("jobs", [])
    for job in jobs:
        if RECOMMENDED_JOB_MARKER in job.get("name", "") and job.get("status") == "success":
            return job
    for job in jobs:
        if job.get("status") == "success":
            return job
    raise RuntimeError(f"No successful job found in AppVeyor build {build.get('version')}")


def get_job_zip_artifact(job_id: str) -> dict:
    url = f"https://ci.appveyor.com/api/buildjobs/{job_id}/artifacts"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    artifacts = resp.json()
    for art in artifacts:
        if art.get("fileName", "").lower().endswith(".zip"):
            return art
    raise RuntimeError(f"No .zip artifact found for AppVeyor job {job_id}")


def gitflic_headers() -> dict:
    if not GITFLIC_TOKEN:
        raise RuntimeError("GITFLIC_API_TOKEN environment variable is not set")
    return {"Authorization": f"token {GITFLIC_TOKEN}"}


def gitflic_tag_exists(tag_name: str) -> bool:
    url = f"{GITFLIC_API_BASE}/project/{GITFLIC_OWNER}/{GITFLIC_PROJECT}/tag/{tag_name}"
    resp = requests.get(url, headers=gitflic_headers(), timeout=30)
    if resp.status_code == 200:
        return True
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    return False


def create_gitflic_tag(tag_name: str, commit_id: str, message: str) -> dict:
    url = f"{GITFLIC_API_BASE}/project/{GITFLIC_OWNER}/{GITFLIC_PROJECT}/tag/create"
    payload = {
        "tagName": tag_name,
        "commitId": commit_id,
        "message": message,
    }
    resp = requests.post(url, json=payload, headers=gitflic_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def create_gitflic_release(tag_name: str, title: str, description: str) -> str:
    url = f"{GITFLIC_API_BASE}/project/{GITFLIC_OWNER}/{GITFLIC_PROJECT}/release"
    payload = {
        "title": title,
        "description": description,
        "tagName": tag_name,
        "preRelease": True,
        "attachProjectArchive": False,
    }
    resp = requests.post(url, json=payload, headers=gitflic_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    release_uuid = data.get("id") or data.get("uuid")
    if not release_uuid:
        raise RuntimeError(f"Unexpected release response (missing id/uuid): {data}")
    return release_uuid


def upload_release_file(release_uuid: str, file_path: Path) -> None:
    url = f"{GITFLIC_API_BASE}/project/{GITFLIC_OWNER}/{GITFLIC_PROJECT}/release/{release_uuid}/file"
    with file_path.open("rb") as fh:
        files = {"files": (file_path.name, fh, "application/zip")}
        resp = requests.post(url, files=files, headers=gitflic_headers(), timeout=120)
    resp.raise_for_status()


def main() -> int:
    build = get_latest_appveyor_build()
    status = build.get("status")
    version = build.get("version")
    commit_id = build.get("commitId")

    if status != "success":
        print(f"Latest AppVeyor build ({version}) status is '{status}', skipping.")
        return 0

    tag_name = str(version)
    if gitflic_tag_exists(tag_name):
        print(f"Tag '{tag_name}' already exists on GitFlic ({GITFLIC_OWNER}/{GITFLIC_PROJECT}). Nothing to do.")
        return 0

    job = pick_recommended_job(build)
    job_id = job["jobId"]
    artifact = get_job_zip_artifact(job_id)
    file_name = artifact["fileName"]
    artifact_url = f"https://ci.appveyor.com/api/buildjobs/{job_id}/artifacts/{file_name}"
    build_url = f"https://ci.appveyor.com/project/{APPVEYOR_ACCOUNT}/{APPVEYOR_PROJECT}/builds/{build['buildId']}/job/{job_id}/artifacts"

    print(f"New build detected: {version} (commit {commit_id}), downloading {file_name}...")
    with tempfile.TemporaryDirectory() as tmpdir:
        local_zip = Path(tmpdir) / Path(file_name).name
        with requests.get(artifact_url, stream=True, timeout=120) as dl:
            dl.raise_for_status()
            with local_zip.open("wb") as out:
                for chunk in dl.iter_content(chunk_size=65536):
                    out.write(chunk)

        print(f"Creating tag '{tag_name}' on GitFlic...")
        create_gitflic_tag(
            tag_name=tag_name,
            commit_id=commit_id,
            message=f"Automated AppVeyor build {version}",
        )

        description = (
            f"Automated mirror of AppVeyor build **{version}** from `otya128/winevdm`.\n\n"
            f"- Commit: `{commit_id}`\n"
            f"- AppVeyor Artifacts: {build_url}"
        )
        print(f"Creating release '{tag_name}' on GitFlic...")
        release_uuid = create_gitflic_release(
            tag_name=tag_name,
            title=f"otvdm {version}",
            description=description,
        )

        print(f"Uploading {local_zip.name} to release {release_uuid}...")
        upload_release_file(release_uuid, local_zip)

    print(f"Successfully published {version} to GitFlic!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
