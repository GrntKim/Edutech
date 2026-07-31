from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"msg": "hello, World"}
    )
