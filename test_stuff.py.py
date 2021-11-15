"""
handlers for cases in incoming call
"""


from krispcall.common.adapters.countries_list import get_country_by_number
from krispcall.common.core.broadcaster import KrispBroadcast
from krispcall.foundation.service_layer.views import (
    get_online_members,
)
import typing
from uuid import UUID
from dumsi.databases.protocols import DbConnection
from krispcall.common.core.bootstrap import JobQueue
from dumsi.web.starlette.helpers import get_database
from krispcall.channel.domain import models
from dumsi.core.domain.value_objects import ShortId

# from sqlalchemy.sql.functions import user
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import Response
from krispcall.channel.service_layer.helpers.broadcast import (
    broadcast_conversation,
)
from shortuuid import ShortUUID
from krispcall.channel import services
from krispcall.channel.service_layer import abstracts
from krispcall_twilio import TwilioClient
from krispcall.channel.service_layer import views
from krispcall.channel.service_layer.helpers import static_helpers
from twilio.twiml.voice_response import VoiceResponse
from krispcall.device.service_layer import views as div_views
from krispcall.client.service_layer import views as client_views
from krispcall.billing.service_layer.helpers import billing_plan_restriction
from krispcall.channel.domain.models import Conversation, GreetingType
from krispcall.billing.service_layer.helpers.business_rules import (
    THRESHOLD_CALL_INCOMING,
)
from krispcall.provider.services import add_and_publish_new_leads_count
from krispcall.provider.services import create_conversation

import logging

logger = logging.getLogger("twilio")

# from webapi.pubsub.conversation import conversation_count_updates


async def handle_client_blocked_on_call(
    workspace_id: UUID,
    client_number: str,
    call_sid: str,
    db_conn: DbConnection,
    queue: JobQueue,
) -> Response:
    """[Return Twiml for the Busy/ Blocked Response]

    Args:
        data (abstracts.AddTwilioConversation): [description]
        db_conn (DbConnection): [description]

    Raises:
        client_exceptions.ContactPhoneNumberBlocked: [description]
    """
    reject_ = VoiceResponse()
    # change this to the block response of client
    reject_.say(message="Sorry. The number you have dialed, is busy.")
    charge_resource_data = {
        "workspace_id": workspace_id,
        "call_sid": call_sid,
    }
    await queue.run_task(
        "task_call_charge_resource",
        charge_resource_data,
        _queue_name="arq:payment_queue",
    )

    logger.debug(f"{client_number} is blocked so playing busy response")
    return Response(
        status_code=200,
        content=str(reject_),
        media_type="application/xml",
    )
    # raise client_exceptions.ContactPhoneNumberBlocked(
    #     "Blocked Phone Number"
    # )


async def handle_all_agents_in_dnd(
    data: FormData,
    workspace_id: UUID,
    channel_id: UUID,
    request: Request,
    settings,
) -> Response:
    call_data: abstracts.AddTwilioConversation = abstracts.AddTwilioConversation(
        string_id=data["CallSid"],
        conversation_type="Call",
        content={},
        from_number=data["From"],
        to_number=data["To"],
        workspace_id=workspace_id,
        channel_id=channel_id,
        conversation_status="NoAnswer",
    )
    conversation = await services.new_twilio_conversation(
        validated_data=call_data, db_conn=get_database(request)
    )

    await broadcast_conversation(
        conversation,
        request.app.state.broadcast,
        get_database(request=request),
        new_conversation=True,
    )
    recording_url = await views.active_channel_greetings(
        channel=call_data.channel_id,
        greeting_type=GreetingType.voicemail.value,
        db_conn=get_database(request=request),
    )
    client = request.app.state.twilio

    # Modify the call to record voice mail
    # Channel transcription setting.
    if settings:
        transcription_settings = (
            settings.get("transcription") if settings.get("transcription") else False
        )
    voice_resp = await client.record_voice_mail(
        workspace_sid=ShortId.with_uuid(workspace_id),
        channel_sid=ShortId.with_uuid(channel_id),
        recording_url=recording_url,
        transcribe=transcription_settings if transcription_settings else False,
    )

    # send if first time caller or recurring caller
    channel = channel_id
    contact = data["From"]
    workspace = workspace_id
    # move it to background job
    await services.auto_reply_to_client(channel, contact, workspace, request)

    # Add charge resource if dnd enabled
    charge_resource_data = {
        "workspace_id": workspace_id,
        "call_sid": data["CallSid"],
    }
    await request.app.state.queue.run_task(
        "task_call_charge_resource",
        charge_resource_data,
        _queue_name="arq:payment_queue",
    )
    # End Add charge resource if dnd enabled

    logger.debug("All agent have dnd enabled.")
    logger.debug(call_data)
    return Response(
        status_code=200, media_type="application/xml", content=str(voice_resp)
    )


async def handle_no_active_agents(
    workspace_id: UUID,
    channel_id: UUID,
    recording_url: str,
    twilio_client: TwilioClient,
    conversation: Conversation,
    database: DbConnection,
    broadcast: KrispBroadcast,
    transcription: bool = False,
) -> Response:
    print("Triggered!!!")
    print(type(conversation))
    voice_resp = await twilio_client.record_voice_mail(
        workspace_sid=ShortId.with_uuid(workspace_id),
        channel_sid=ShortId.with_uuid(channel_id),
        recording_url=recording_url,
        transcribe=transcription,
    )
    logger.debug("No active agents.")
    conversation = await services.update_conversation_status(
        id_=conversation.id_,
        validated_data=abstracts.UpdateConversationEvents(
            conversation_status="NoAnswer", string_id=conversation.string_id
        ),
        db_conn=database,
    )
    await broadcast_conversation(
        conversation=conversation,
        broadcast=broadcast,
        db_conn=database,
        new_conversation=True,
    )
    return Response(
        status_code=200,
        media_type="application/xml",
        content=str(voice_resp),
    )


async def handle_dial_to_active_agents(
    conversation: models.Conversation,
    data: FormData,
    active_agents: typing.List[UUID],
    request: Request,
) -> Response:
    channel_id = request.path_params["channel_sid"]
    base_url = request.app.state.settings.app_uri
    url = base_url + "/numbers/" + channel_id
    workspace_id = request.path_params["workspace_sid"]
    workspace_id = ShortId(workspace_id).uuid()

    channel_id = ShortUUID().decode(request.path_params["channel_sid"])
    active_agents_sid = [ShortId.with_uuid(agent) for agent in active_agents]
    twilio_client: TwilioClient = request.app.state.twilio

    db_conn = get_database(request)
    broadcast = request.app.state.broadcast

    settings = await views.get_channel_settings(channel=channel_id, db_conn=db_conn)
    auto_record: bool = settings.get("auto_record_calls") if settings else False

    # NEEDS huge refactor

    conversation_response = await broadcast_conversation(
        conversation,
        broadcast,
        db_conn,
        new_conversation=True,
    )
    # send push notification on incoming call

    # If agents have DND in channel, send missed notification
    # user_id = conversation_response.get("data").get("auth_id")

    notification_enable_agents = await div_views.get_device_agent(
        active_agents, db_conn
    )
    agents = [
        dict(agent).get("id")
        for agent in notification_enable_agents
        if dict(agent).get("call_messages")
    ]
    new_agents = [
        dict(agent).get("id")
        for agent in notification_enable_agents
        if dict(agent).get("new_leads")
    ]

    client_number = conversation_response.get("data").get("client_number")

    number_exists = await client_views.client_number_exists(
        workspace_id=workspace_id,
        number=client_number,
        db_conn=get_database(request),
    )

    if not number_exists:
        await request.app.state.queue.run_task(
            "notify_conversation_to_agents",
            new_agents,
            conversation.id_,  # source
            workspace_id,
            url,
            "call",  # event type
            request.app.state.fcmservice,
        )
    if number_exists:
        await request.app.state.queue.run_task(
            "notify_conversation_to_agents",
            agents,
            conversation.id_,  # source
            workspace_id,
            url,
            "call",  # event type
            request.app.state.fcmservice,
        )

    channel_data = await views.get_channel_info(channel_id=channel_id, db_conn=db_conn)

    channel_number = channel_data.get("number")
    country = await get_country_by_number(channel_number)

    country_id = country.get("uid")
    channel_country_flag = country.get("flag_url")
    country_code = country.get("dialing_code")
    channel_info = dict(
        id=channel_data.get("id"),
        name=channel_data.get("name"),
        number=channel_data.get("number"),
        country=ShortId.with_uuid(country_id),
        country_logo=channel_country_flag,
        country_code=country_code,
    )

    # Play: get recording for welcome message
    recording_url = await views.active_channel_greetings(
        channel=channel_id,
        greeting_type=GreetingType.welcome.value,
        db_conn=db_conn,
    )

    # if forwarding to external numbere is enabled.
    external_forward = False
    if settings.get("incoming_call_forward").lower() == "externalnumber":
        external_forward = True

    response = await twilio_client.incoming_call_handler(
        auto_record=auto_record,
        call_to=active_agents_sid,
        call_from=data["From"],
        workspace_sid=request.path_params["workspace_sid"],
        channel_sid=request.path_params["channel_sid"],
        welcome_recording_url=recording_url,
        params={
            "after_hold": "False",
            "conversation_id": ShortId.with_uuid(conversation.id_),
            "contact_number": data["From"],
            "channel_sid": request.path_params["channel_sid"],
            "channel_info": channel_info,
        },
        external_forward=external_forward,
        external_number=settings.get("external_number"),
        simultaneous_dial=settings.get("simultaneous_dialing"),
    )

    return Response(
        status_code=200,
        content=str(response),
        media_type="application/xml",
    )


async def handle_incoming_call(data: FormData, request: Request) -> Response:
    channel_id = ShortId(request.path_params["channel_sid"]).uuid()
    workspace_id = ShortId(request.path_params["workspace_sid"]).uuid()
    db_conn = get_database(request)
    queue = request.app.state.queue
    call_sid = data["CallSid"]
    call_data: abstracts.AddTwilioConversation = abstracts.AddTwilioConversation(
        string_id=data["CallSid"],
        conversation_type="Call",
        content={},
        from_number=data["From"],
        to_number=data["To"],
        workspace_id=workspace_id,
        channel_id=channel_id,
        conversation_status=data["CallStatus"].title(),
    )
    logger.debug("New incoming call")
    # logger.debug("Call data:", call_data)

    settings = await views.get_channel_settings(
        channel=ShortId(request.path_params["channel_sid"]).uuid(),
        db_conn=get_database(request),
    )
    # Deduct due amount from credit if available
    # check credit
    price_per_min = THRESHOLD_CALL_INCOMING
    deduct_due_amount_data = {
        "workspace": ShortId(request.path_params["workspace_sid"]).uuid(),
    }
    await request.app.state.queue.run_task(
        "task_deduct_due_amount",
        deduct_due_amount_data,
        _queue_name="arq:payment_queue",
    )
    low_credit_message = billing_plan_restriction.low_credit_message("incoming")
    insufficient_credit = await billing_plan_restriction.check_credit_per_minute(
        workspace_id=workspace_id,
        price_per_minute=price_per_min,
        db_conn=db_conn,
    )
    if insufficient_credit:
        return await handle_insufficient_credit(
            workspace_id=workspace_id,
            call_sid=call_sid,
            message=low_credit_message,
            queue=queue,
            db_conn=db_conn,
        )
    number_blocked = await client_views.phone_number_blocked(
        workspace_id=workspace_id,
        number=call_data.from_number,
        db_conn=db_conn,
    )
    if number_blocked:
        # return number is busy response
        return await handle_client_blocked_on_call(
            workspace_id=call_data.workspace_id,
            client_number=call_data.from_number,
            call_sid=call_sid,
            queue=queue,
            db_conn=db_conn,
        )

    # Forward call to external number if external forwarding
    # is enabled.
    (
        dnd_enabled_agents,
        available_agents,
    ) = await services.get_agents_dnd_information(channel_id, db_con=db_conn)
    # todo: filter online members from dnd_disabled
    if available_agents:  # with no dnd/ can accept call
        online_agents = await get_online_members(
            members=available_agents, db_conn=db_conn
        )

        # Get agents dnd status for a client
        active_agents = await static_helpers.filter_agent_by_client_dnd(
            agents_record=online_agents,
            contact=call_data.from_number,
            workspace_id=workspace_id,
            db_conn=db_conn,
        )
        # Get agents who are not on call right now

        # If all the agents have dnd activated for the client.
        # Redirect the call to voicemail
        # Play: Replace the msg with recording url

        recording_url = await views.active_channel_greetings(
            channel=channel_id,
            greeting_type=GreetingType.voicemail.value,
            db_conn=db_conn,
        )

        # Conversation is created here to update later in callbacks
        conversation = await create_conversation(data, available_agents, request)
        # new leads count
        # should refactor as a background task
        await add_and_publish_new_leads_count(
            channel=channel_id,
            agents=active_agents,
            workspace=workspace_id,
            db_conn=db_conn,
            broadcast=request.app.state.broadcast,
        )

        if len(active_agents) < 1:
            return await handle_no_active_agents(
                channel_id=channel_id,
                workspace_id=workspace_id,
                recording_url=recording_url,
                twilio_client=request.app.state.twilio,
                conversation=conversation,
                database=db_conn,
                broadcast=request.app.state.broadcast,
                transcription=settings.get("transcription"),
            )

        # broadcast conversation 1st time
        # await broadcast_conversation(
        #     conversation=conversation,
        #     broadcast=request.app.state.broadcast,
        #     db_conn=db_conn,
        #     new_conversation=True,
        # )

        # if sip forwarding is enabled
        # Refer to sip address
        # If agents have DND in channel, send missed notification
        if dnd_enabled_agents:
            await services.create_agents_conversation_dnd_missed(
                conversation=conversation,
                agents=tuple(dnd_enabled_agents),
                db_conn=db_conn,
            )
        return await handle_dial_to_active_agents(
            conversation=conversation,
            data=data,
            active_agents=active_agents,
            request=request,
        )
    elif dnd_enabled_agents:
        return await handle_all_agents_in_dnd(
            data, workspace_id, channel_id, request, settings
        )

    return Response(status_code=200)


async def handle_insufficient_credit(
    workspace_id: UUID,
    call_sid: str,
    message: str,
    db_conn: DbConnection,
    queue: JobQueue,
) -> Response:
    # Add charge resource if workspace have low credit
    charge_resource_data = {
        "workspace_id": workspace_id,
        "call_sid": call_sid,
    }
    await queue.run_task(
        "task_call_charge_resource",
        charge_resource_data,
        _queue_name="arq:payment_queue",
    )
    # End Add charge resource if workspace have low credit
    voice_resp = VoiceResponse()
    voice_resp.say(message=message)

    logger.debug("Insufficient credit for call {call_sid}")
    return Response(
        status_code=200,
        content=str(voice_resp),
        media_type="application/xml",
    )
