import asyncio
import json

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.post("/backend-api/conversation")
async def mock_conversation() -> StreamingResponse:
    async def generate():
        for i in range(5):
            chunk = f"data: {json.dumps({'message': {'content': {'parts': [f'Token {i}']}}})}\n\n"
            yield chunk.encode()
            await asyncio.sleep(0.05)
        yield b"data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/public-api/me")
async def mock_me() -> dict[str, str]:
    return {"id": "user-123", "email": "test@example.com"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
