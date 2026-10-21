import os
from dotenv import load_dotenv
from openrouter import OpenRouter

load_dotenv()

api_key = os.getenv("OPENROUTER_API_KEY")

if not api_key:
    print("❌ API key not found!")
    exit()

print("✅ API key found!")
print("🤖 Sending message to AI...")

with OpenRouter(api_key=api_key) as client:
    response = client.chat.send(
        model="nvidia/nemotron-3.5-lightning:free",
        messages=[
            {
                "role": "user",
                "content": "I am not feeling well, can you help me?",
            }
        ]
    )

print("\n🤖 AI response:")
print(response.choices[0].message.content)