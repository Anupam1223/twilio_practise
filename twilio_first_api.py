from cryptography.fernet import Fernet
from twilio.twiml.voice_response import VoiceResponse
from fastapi import FastAPI
from fastapi.responses import Response
import uvicorn
from encryption import decrypt_token

app = FastAPI()


@app.post("/return_response/{msg}")
def return_response(msg) -> Response:
    value = decrypt_token(token=msg)
    response_value = format_voice_response(value)
    return Response(
        content=response_value, media_type="application/xml", status_code=201
    )


def format_voice_response(msg: str) -> str:
    say_me = VoiceResponse()
    say_me.say(msg)
    return str(say_me)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
