"""The language this browser shows the site in, once a translation is installed (i18n.py, docs/translating.md):
More → Language, kept in a cookie. Without one picked, the browser's own languages decide."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from lineagebox import errors, i18n
from lineagebox.web.common import MEMBER, _from_app

router = APIRouter()
ACCESS = {
    "/api/language": MEMBER,
}


class LanguageBody(BaseModel):
    lang: str = ""  # one of i18n.installed(); "" for the browser's own choice again


@router.post("/api/language", dependencies=[Depends(_from_app)])
def choose_language(request: Request, body: LanguageBody):
    if body.lang and body.lang not in i18n.installed():
        raise errors.Problem(i18n.gettext("That language isn't installed here."))
    response = JSONResponse({"reload": True})
    if body.lang:
        response.set_cookie(i18n.COOKIE, body.lang, max_age=365 * 86400, samesite="lax",
                            secure=request.url.scheme == "https")
    else:
        response.delete_cookie(i18n.COOKIE)
    return response
