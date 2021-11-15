from twilio.twiml.voice_response import VoiceResponse
from fastapi import FastAPI
from fastapi.responses import Response
import uvicorn

app = FastAPI()


@app.post("/return_response")
def return_response() -> Response:
    msg = format_voice_response("Here I am. Here I will remain.")
    return Response(content=msg, media_type="application/xml", status_code=201)


def format_voice_response(msg: str) -> str:
    say_me = VoiceResponse()
    say_me.say(msg)
    return str(say_me)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
