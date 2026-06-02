import logging
import os
import random

logger = logging.getLogger(__name__)

ALIBABA_ACCESS_KEY_ID = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
ALIBABA_ACCESS_KEY_SECRET = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
SMS_SIGN_NAME = os.getenv("SMS_SIGN_NAME", "产品脑暴工作台")
SMS_TEMPLATE_CODE = os.getenv("SMS_TEMPLATE_CODE", "SMS_274000000")


def send_sms_via_alibaba(phone: str, code: str) -> dict:
    if not ALIBABA_ACCESS_KEY_ID or not ALIBABA_ACCESS_KEY_SECRET:
        return {"success": False, "reason": "not_configured"}

    try:
        from alibabacloud_dysmsapi20170525.client import Client
        from alibabacloud_dysmsapi20170525 import models as sms_models
        from alibabacloud_tea_openapi import models as open_api_models

        config = open_api_models.Config(
            access_key_id=ALIBABA_ACCESS_KEY_ID,
            access_key_secret=ALIBABA_ACCESS_KEY_SECRET,
        )
        config.endpoint = "dysmsapi.aliyuncs.com"
        client = Client(config)

        request = sms_models.SendSmsRequest(
            phone_numbers=phone,
            sign_name=SMS_SIGN_NAME,
            template_code=SMS_TEMPLATE_CODE,
            template_param=f'{{"code":"{code}"}}',
        )
        response = client.send_sms(request)

        if response.body.code == "OK":
            logger.info(f"SMS sent to {phone[:3]}****{phone[-4:]}")
            return {"success": True}
        else:
            logger.warning(f"SMS failed: {response.body.code} - {response.body.message}")
            return {"success": False, "reason": response.body.code, "message": response.body.message}
    except Exception as e:
        logger.error(f"SMS send error: {e}")
        return {"success": False, "reason": "exception", "message": str(e)}
