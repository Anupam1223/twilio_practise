from twilio.rest import Client
from encryption import encrypt_token

account_sid = "xxxxxxxx"
auth_token = "xxxxxxxx"
client = Client(account_sid, auth_token)
from_number = "+xxxxx"
to_number = "+xxxxx"


def make_phone_call(client_, from_phone_number_, to_phone_number_):
    val = input("what do you want to say")
    msg = encrypt_token(token=val)
    call = client_.calls.create(
        record=True,
        url=f"http://0a4f-202-51-76-55.in.ngrok.io/return_response/{msg}",
        from_=from_phone_number_,
        to=to_phone_number_,
    )
    return call.sid


print(make_phone_call(client, from_number, to_number))
