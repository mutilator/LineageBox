"""AI on request: photos described again (one, or the ticked ones; an admin may pick another of Ollama's models), the
models to pick from, and the photos whose descriptions are closest to one's (More like this)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from lineagebox import describe, embeddings, settings
from lineagebox.llm import Ollama
from lineagebox.web.common import ADMIN, MEMBER, _from_app, _image_or_404, _images_or_404, _photos
from lineagebox.web.deps import con, page, site, viewer, writer
from lineagebox.web.site import Site

router = APIRouter()
ACCESS = {
    "/api/images/{image_id}/describe-again": MEMBER,
    "/api/selection/describe-again": MEMBER,
    "/api/ai/models": ADMIN,
    "/image/{image_id}/like": MEMBER,
}
MODELS_FOR = 300  # seconds the list of Ollama's models is kept: listing it asks Ollama about each one


class AgainBody(BaseModel):
    """Describe again: the model an admin picked ("" for the Settings page's). The tick-photos bar sends the ticked
    photos too, and its other boxes, which are left alone."""
    ai_model: str = Field("", max_length=100)


class AgainSelection(AgainBody):
    image_ids: list[int] = Field(min_length=1, max_length=5000)


def _ask(s: Site, v, c, w, image_ids: list[int], model: str) -> dict:
    """Put photos on the worker's list to describe again (describe.ask_again) and start a pass. Members ask with the
    Settings page's model; an admin may name another."""
    now = s.live(c)
    if not now.describe_enabled:
        raise HTTPException(409, "Describing with AI is switched off on the Settings page.")
    model = "" if v.limited else model.strip()
    if model:
        model = settings._CHECKS["ollama_model"](model, now)  # a name as Ollama lists them, or Invalid
    if model.removesuffix(":latest") == now.ollama_model.removesuffix(":latest"):
        model = ""
    n = describe.ask_again(w, image_ids, model or None, v.account_id)
    if not n:
        raise HTTPException(404, "No such photo")
    s.ask_worker()
    when = "soon"
    if not settings.may_describe(now, datetime.now()):
        start = settings.next_describe_start(now, datetime.now())
        when = (f"from {start:%H:%M}{'' if start.date() == datetime.now().date() else f' on {start:%A}'}, when the "
                f"describing hours start" if start else "once describing is no longer paused (Settings → Schedule)")
    what = "This photo" if n == 1 else _photos(n)
    with_ = f" with {model}" if model else ""
    return {"message": f"{what} will be described again{with_} {when}. The words people wrote and the tags they "
                       f"gave or took off stay as they are." + (" Status shows how many are waiting." if v.admin
                                                                else "")}


@router.post("/api/images/{image_id}/describe-again", dependencies=[Depends(_from_app)])
def describe_again(image_id: int, body: AgainBody | None = None, v=Depends(viewer), c=Depends(con),
                   w=Depends(writer), s: Site = Depends(site)):
    _image_or_404(c, image_id)
    return _ask(s, v, c, w, [image_id], (body or AgainBody()).ai_model)


@router.post("/api/selection/describe-again", dependencies=[Depends(_from_app)])
def describe_selection_again(body: AgainSelection, v=Depends(viewer), c=Depends(con), w=Depends(writer),
                             s: Site = Depends(site)):
    _images_or_404(c, v, body.image_ids)
    return _ask(s, v, c, w, body.image_ids, body.ai_model)


@router.get("/api/ai/models")
def ai_models(v=Depends(viewer), c=Depends(con), s: Site = Depends(site)):
    """The models of Ollama's that can look at pictures, to describe again with (app.js data-suggest): kept a few
    minutes; none while Ollama doesn't answer."""
    now = s.live(c)
    found = s.keep(v, ("ai models", now.ollama_url), MODELS_FOR, lambda: Ollama(now, timeout=10).models())
    return [m["name"] for m in found if m["vision"] and not m["embedding"]]


@router.get("/image/{image_id}/like", response_class=HTMLResponse)
def like_this(request: Request, image_id: int, c=Depends(con), s: Site = Depends(site)):
    """More like this, on a photo's page (data-later): the photos the viewer sees whose descriptions are closest to
    this one's, from other days as much as there are. Nothing while search by meaning is off or this photo has no
    description read yet."""
    _image_or_404(c, image_id)
    found = embeddings.like_this(c, s.live(c), image_id)
    return page(request, "_like_this.html", like=found, image_id=image_id)
