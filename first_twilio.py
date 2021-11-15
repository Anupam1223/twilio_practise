from starlette.datastructures import URL
from twilio.rest import Client
from twilio_first_api import format_voice_response

account_sid = "ACa202767123ac67b5b13ebeccb2377c9d"
auth_token = "1f2adda1b3be014001e175bb00af90f9"
client = Client(account_sid, auth_token)
from_number = "+12564877123"
to_number = "+9779814952888"


def make_phone_call(client_, from_phone_number_, to_phone_number_):
    val = input("what do you want to say")
    phone_call = format_voice_response(
        "Hello, is the end of the world? Cause it kinda feels like it."
    )
    call = client_.calls.create(
        record=True,
        url="http://78a6-27-34-20-37.in.ngrok.io/return_response",
        from_=from_phone_number_,
        to=to_phone_number_,
    )
    return call.sid


print(make_phone_call(client, from_number, to_number))
