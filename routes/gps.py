from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from pymongo import MongoClient
from datetime import datetime
from dotenv import load_dotenv
import xmltodict
import os

load_dotenv()

# Configura tu conexión a MongoDB
client = MongoClient(os.getenv("MONGODB_URI"))
db = client["globalStar"]
collection = db["datos"]


router = APIRouter()

# Recibir datos de la API
@router.post("/gpsApi", response_class=PlainTextResponse)
async def receive_stu_messages(request: Request):
    xml_data = await request.body()
    try:
        parsed = xmltodict.parse(xml_data)
        stu_messages = parsed.get("stuMessages", {})
        message_id = stu_messages.get("@messageID")
        time_stamp = stu_messages.get("@timeStamp")
        messages = stu_messages.get("stuMessage", [])

        # Si solo hay un mensaje, convertir a lista
        if isinstance(messages, dict):
            messages = [messages]

        inserted_count = 0
        for msg in messages:
            doc = {
                "message_id": message_id,
                "time_stamp": time_stamp,
                "esn": msg.get("esn"),
                "unixTime": msg.get("unixTime"),
                "gps": msg.get("gps"),
                "payload": msg.get("payload", {}).get("#text") if isinstance(msg.get("payload"), dict) else msg.get("payload"),
                "payload_length": msg.get("payload", {}).get("@length") if isinstance(msg.get("payload"), dict) else None,
                "payload_source": msg.get("payload", {}).get("@source") if isinstance(msg.get("payload"), dict) else None,
                "payload_encoding": msg.get("payload", {}).get("@encoding") if isinstance(msg.get("payload"), dict) else None
            }
            collection.insert_one(doc)
            inserted_count += 1

        # Construir XML de respuesta
        delivery_time = datetime.utcnow().strftime("%d/%m/%Y %H:%M:%S GMT")
        correlation_id = message_id or "unknown"
        response_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
                        <stuResponseMsg xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                        xsi:noNamespaceSchemaLocation="http://cody.glpconnect.com/XSD/StuResponse_Rev1_0.xsd"
                        deliveryTimeStamp="{delivery_time}" messageID="{message_id}" correlationID="{correlation_id}">
                        <state>pass</state>
                        <stateMessage>{inserted_count} messages received and stored successfully</stateMessage>
                        </stuResponseMsg>
                        """
        return Response(content=response_xml, media_type="text/xml")

    except Exception as e:
        error_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
                    <stuResponseMsg xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                    xsi:noNamespaceSchemaLocation="http://cody.glpconnect.com/XSD/StuResponse_Rev1_0.xsd"
                    deliveryTimeStamp="{datetime.utcnow().strftime("%d/%m/%Y %H:%M:%S GMT")}" messageID="unknown" correlationID="unknown">
                    <state>fail</state>
                    <stateMessage>{str(e)}</stateMessage>
                    </stuResponseMsg>
                    """
        return Response(content=error_xml, media_type="text/xml", status_code=400)

