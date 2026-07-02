"""
Autodesk Platform Services (APS) — DWG to PDF conversion via Design Automation API.

Uses the built-in AutoCAD.PlotToPDF shared activity to run a headless AutoCAD
engine in the cloud for production-quality PDF output.

Flow:
  1. Get APS OAuth2 token (2-legged, with code:all scope)
  2. Ensure OSS bucket exists
  3. Upload DWG via 3-step Direct-to-S3 signed upload
  4. Generate signed GET URL for source DWG
  5. Generate signed PUT URL for result PDF
  6. Submit WorkItem to AutoCAD.PlotToPDF
  7. Poll WorkItem until complete
  8. Download PDF from OSS bucket

Requires APS_CLIENT_ID, APS_CLIENT_SECRET, APS_BUCKET_KEY configured in settings.
"""

import base64
import time
from pathlib import Path

import requests as http_requests

from app.core.logging import logger
from app.core.settings import settings

APS_BASE_URL = "https://developer.api.autodesk.com"
DA_REGION = "us-east"


def _is_aps_configured() -> bool:
    return bool(settings.aps_client_id and settings.aps_client_secret)


def _get_aps_token() -> str:
    """Obtain a 2-legged OAuth token with code:all scope for Design Automation."""
    url = f"{APS_BASE_URL}/authentication/v2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_id": settings.aps_client_id,
        "client_secret": settings.aps_client_secret,
        "grant_type": "client_credentials",
        "scope": "bucket:create bucket:read data:write data:read code:all",
    }
    response = http_requests.post(url, headers=headers, data=data, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"APS auth failed (HTTP {response.status_code}): {response.text[:500]}")
    token = response.json()["access_token"]
    logger.info("aps_token_obtained")
    return token


def _ensure_bucket(token: str) -> None:
    """Create the OSS bucket if it does not already exist (transient = 24h expiry)."""
    url = f"{APS_BASE_URL}/oss/v2/buckets"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"bucketKey": settings.aps_bucket_key, "policyKey": "transient"}
    response = http_requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code == 409:
        logger.info("aps_bucket_already_exists", bucket=settings.aps_bucket_key)
    elif response.status_code != 200:
        raise RuntimeError(f"APS bucket creation failed (HTTP {response.status_code}): {response.text[:500]}")
    else:
        logger.info("aps_bucket_created", bucket=settings.aps_bucket_key)


def _upload_dwg(token: str, source_path: Path) -> str:
    """3-step Direct-to-S3 signed upload to OSS bucket."""
    filename = source_path.name
    auth_header = {"Authorization": f"Bearer {token}"}

    # Step 1: Request signed S3 upload URL
    signed_url = (
        f"{APS_BASE_URL}/oss/v2/buckets/{settings.aps_bucket_key}"
        f"/objects/{filename}/signeds3upload"
    )
    signed_resp = http_requests.get(
        signed_url, headers=auth_header, params={"minutesExpiration": 15}, timeout=30
    )
    if signed_resp.status_code != 200:
        raise RuntimeError(
            f"APS signed URL request failed (HTTP {signed_resp.status_code}): {signed_resp.text[:500]}"
        )
    signed_data = signed_resp.json()
    upload_key = signed_data.get("uploadKey")
    urls = signed_data.get("urls", [])
    if not urls or not upload_key:
        raise RuntimeError(f"APS signed URL response missing uploadKey or urls: {signed_resp.text[:500]}")

    # Step 2: Upload to pre-signed S3 URL (unauthenticated PUT)
    with open(source_path, "rb") as f:
        s3_resp = http_requests.put(urls[0], data=f, timeout=300)
    if s3_resp.status_code not in (200, 201):
        raise RuntimeError(f"APS S3 upload failed (HTTP {s3_resp.status_code}): {s3_resp.text[:500]}")

    # Step 3: Complete upload
    complete_resp = http_requests.post(
        signed_url,
        headers={**auth_header, "Content-Type": "application/json"},
        json={"uploadKey": upload_key},
        timeout=30,
    )
    if complete_resp.status_code != 200:
        raise RuntimeError(
            f"APS upload completion failed (HTTP {complete_resp.status_code}): {complete_resp.text[:500]}"
        )

    # Verify upload completed
    complete_data = complete_resp.json()
    object_id = complete_data.get("objectId")
    if not object_id:
        results = complete_data.get("results", [])
        if results:
            object_id = results[0].get("objectId")
    if not object_id:
        raise RuntimeError(
            f"Could not extract objectId from completion response: {complete_resp.text[:500]}"
        )

    logger.info("aps_upload_success", filename=filename, object_id=object_id[:80])
    return filename  # Return the filename for subsequent operations


def _generate_signed_url(token: str, filename: str, access: str = "read") -> str:
    """Generate a signed URL for reading an object in the OSS bucket."""
    url = (
        f"{APS_BASE_URL}/oss/v2/buckets/{settings.aps_bucket_key}"
        f"/objects/{filename}/signed"
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"access": access}
    response = http_requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(
            f"APS signed URL generation failed (HTTP {response.status_code}): {response.text[:500]}"
        )
    signed_url = response.json().get("signedUrl")
    if not signed_url:
        raise RuntimeError(f"Signed URL response missing 'signedUrl': {response.text[:200]}")
    logger.info("aps_signed_url_generated", filename=filename, access=access)
    return signed_url


def _submit_workitem(token: str, dwg_filename: str, pdf_filename: str) -> str:
    """Submit a WorkItem to the AutoCAD.PlotToPDF Design Automation activity.
    
    Uses OSS URNs with Authorization headers instead of signed URLs.
    """
    input_urn = f"urn:adsk.objects:os.object:{settings.aps_bucket_key}/{dwg_filename}"
    output_urn = f"urn:adsk.objects:os.object:{settings.aps_bucket_key}/{pdf_filename}"

    url = f"{APS_BASE_URL}/da/{DA_REGION}/v3/workitems"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "activityId": "AutoCAD.PlotToPDF+prod",
        "arguments": {
            "HostDwg": {
                "url": input_urn,
                "verb": "get",
                "headers": {
                    "Authorization": f"Bearer {token}"
                }
            },
            "Result": {
                "url": output_urn,
                "verb": "put",
                "headers": {
                    "Authorization": f"Bearer {token}"
                }
            }
        }
    }

    response = http_requests.post(url, headers=headers, json=payload, timeout=60)
    if response.status_code != 200 and response.status_code != 201:
        raise RuntimeError(
            f"APS WorkItem submission failed (HTTP {response.status_code}): {response.text[:500]}"
        )
    workitem_id = response.json().get("id")
    if not workitem_id:
        raise RuntimeError(f"WorkItem response missing 'id': {response.text[:200]}")
    logger.info("aps_workitem_submitted", workitem_id=workitem_id)
    return workitem_id


def _poll_workitem(token: str, workitem_id: str, timeout_seconds: int = 600) -> None:
    """Poll the WorkItem status until it completes or fails."""
    url = f"{APS_BASE_URL}/da/{DA_REGION}/v3/workitems/{workitem_id}"
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        time.sleep(5)
        response = http_requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(
                f"APS WorkItem status fetch failed (HTTP {response.status_code}): {response.text[:500]}"
            )
        data = response.json()
        status = data.get("status", "")
        report_url = data.get("reportUrl", "")

        if status == "success":
            logger.info("aps_workitem_success", workitem_id=workitem_id, report_url=report_url)
            return
        elif status == "failed" or status == "cancelled":
            error_msg = f"WorkItem {workitem_id} {status}"
            if report_url:
                error_msg += f" — report: {report_url}"
            raise RuntimeError(error_msg)
        else:
            logger.info("aps_workitem_in_progress", workitem_id=workitem_id, status=status)

    raise RuntimeError(f"APS WorkItem timed out after {timeout_seconds}s")


def _download_from_oss(token: str, filename: str, output_path: Path) -> bytes:
    """Download a file from the OSS bucket using a signed read URL."""
    signed_url = _generate_signed_url(token, filename, access="read")
    response = http_requests.get(signed_url, stream=True, timeout=300)
    if response.status_code != 200:
        raise RuntimeError(
            f"APS download failed (HTTP {response.status_code}): {response.text[:500]}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    chunks = [chunk for chunk in response.iter_content(chunk_size=8192) if chunk]
    pdf_bytes = b"".join(chunks)
    output_path.write_bytes(pdf_bytes)
    logger.info("aps_download_success", path=str(output_path), size_bytes=len(pdf_bytes))
    return pdf_bytes


def convert_dwg_to_pdf_via_aps(source_path: Path, output_pdf_path: Path) -> bytes:
    """Full Design Automation pipeline: upload → signed URLs → WorkItem → poll → download.

    Returns the PDF bytes.
    """
    if not _is_aps_configured():
        raise RuntimeError("APS_CLIENT_ID and APS_CLIENT_SECRET are not configured")

    logger.info("aps_convert_start", source=str(source_path))

    token = _get_aps_token()
    _ensure_bucket(token)

    # 1. Upload the DWG to OSS bucket
    dwg_filename = _upload_dwg(token, source_path)

    # 2. Submit WorkItem to headless AutoCAD PlotToPDF using OSS URNs
    pdf_filename = dwg_filename.replace(".dwg", ".pdf")
    workitem_id = _submit_workitem(token, dwg_filename, pdf_filename)

    # 4. Poll for completion
    _poll_workitem(token, workitem_id, timeout_seconds=settings.conversion_timeout_seconds)

    # 5. Download the resulting PDF
    pdf_bytes = _download_from_oss(token, pdf_filename, output_pdf_path)

    logger.info("aps_convert_success", source=str(source_path), pdf_size_bytes=len(pdf_bytes))
    return pdf_bytes