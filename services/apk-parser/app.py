from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

app = FastAPI(title="DeployForge Isolated APK Parser", version="0.1.0")


class ParseRequest(BaseModel):
    artifact_name: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    size_bytes: int = Field(gt=0, le=512 * 1024 * 1024)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "service": "apk-parser"}


@app.post("/api/v1/parse")
def parse_apk(_request: ParseRequest) -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="APK parsing is not available in this release",
    )

