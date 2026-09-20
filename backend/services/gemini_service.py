import json
import logging
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from services.gemini_models import IntentType, StructuredIntent

load_dotenv()
logger = logging.getLogger("gemini_service")

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"


class GeminiService:
    """
    Conversational intent classification service using Google Gemini API.
    Extracts structured customer intent and parameters without performing business calculations.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        self.model = (model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)).strip()
        self._client = None

        if self.api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
                logger.info("Initialized Gemini client with model: %s", self.model)
            except Exception as exc:
                logger.error("Failed to initialize Google Gemini client: %s", exc)
                self._client = None
        else:
            logger.info("No GEMINI_API_KEY provided; Gemini service operating in offline mode.")

    def is_available(self) -> bool:
        """Returns True if Gemini client is initialized with valid credentials."""
        return self._client is not None and bool(self.api_key)

    def _build_prompt(
        self,
        message_text: str,
        history: Optional[List[Dict[str, str]]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        prompt_parts = [
            "You are the NLU / Intent Extraction engine for Mudhra Branding Solutions, a B2B corporate gifting company.",
            "Classify the customer's WhatsApp message into a structured intent and extract any mentioned parameters.",
            "",
            "Intents:",
            "- PRODUCT_SEARCH: Customer searching for products, categories, or budget (e.g. 'I need 100 gift sets under 500', 'show bottles').",
            "- PRODUCT_DETAILS: Customer asking for details about an SKU (e.g. 'Tell me about XG-501', 'colors in GS-001').",
            "- PRICE_QUOTE: Customer asking for price quote or quantity (e.g. 'Give me 200 metal pens', 'Quote for XG-GS-501 100', '100 units').",
            "- SELECT_PRODUCT: Customer selecting from previously listed candidate products (e.g. 'Second one', 'take option 1', 'the first').",
            "- CONFIRM_ORDER: Customer confirming an order (e.g. 'confirm', 'yes please', 'place order', 'proceed').",
            "- CANCEL_ORDER: Customer cancelling (e.g. 'cancel', 'never mind').",
            "- ORDER_STATUS: Customer asking status of an order (e.g. 'order status', 'where is ORD-20260919-0001').",
            "- GENERAL_HELP: Greetings, catalogue menu, or company capabilities.",
            "- UNKNOWN: Irrelevant, ambiguous, or gibberish text.",
            "",
            "Catalogue Categories: Gift Sets, Combos, Writing Instruments / Pens, Water Bottles, Notebooks, Mugs, Electronics, Keychains.",
            "",
            "Selection Index rules:",
            "'first' / '1' / '1st' -> selection_index = 1",
            "'second' / '2' / '2nd' -> selection_index = 2",
            "'third' / '3' / '3rd' -> selection_index = 3",
            "'fourth' / '4' / '4th' -> selection_index = 4",
            "'fifth' / '5' / '5th' -> selection_index = 5",
            "",
        ]

        if context:
            prompt_parts.append(f"Active Conversation Context:\n{json.dumps(context, indent=2)}\n")

        if history:
            prompt_parts.append("Recent Conversation History:")
            for msg in history[-6:]:
                role = "Customer" if msg.get("direction") == "INBOUND" else "Agent"
                prompt_parts.append(f"{role}: {msg.get('message_text')}")
            prompt_parts.append("")

        prompt_parts.append(f"Current Customer Message:\n\"{message_text}\"")
        return "\n".join(prompt_parts)

    def parse_intent(
        self,
        message_text: str,
        history: Optional[List[Dict[str, str]]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> StructuredIntent:
        """
        Extracts structured intent from customer message.
        Guaranteed never to crash: returns UNKNOWN on errors or unavailable service.
        """
        if not message_text or not message_text.strip():
            return StructuredIntent(intent=IntentType.UNKNOWN)

        if not self.is_available():
            logger.warning("Gemini service unavailable; cannot parse intent via LLM.")
            return StructuredIntent(
                intent=IntentType.UNKNOWN,
                notes="Gemini service not configured with API key.",
            )

        prompt = self._build_prompt(message_text, history, context)

        try:
            from google.genai import types

            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=StructuredIntent,
                    temperature=0.0,
                ),
            )

            raw_text = response.text if response and response.text else ""
            if not raw_text:
                logger.warning("Empty response received from Gemini model.")
                return StructuredIntent(intent=IntentType.UNKNOWN, notes="Empty Gemini response")

            # Parse JSON into StructuredIntent
            data = json.loads(raw_text)
            parsed = StructuredIntent(**data)
            logger.info(
                "Gemini classified message '%s' -> intent=%s, sku=%s, qty=%s, idx=%s",
                message_text[:30],
                parsed.intent.value,
                parsed.sku,
                parsed.quantity,
                parsed.selection_index,
            )
            return parsed

        except Exception as exc:
            logger.error("Error during Gemini intent parsing: %s", exc, exc_info=True)
            return StructuredIntent(
                intent=IntentType.UNKNOWN,
                notes=f"Gemini API error: {str(exc)}",
            )


# Singleton instance
gemini_service = GeminiService()
