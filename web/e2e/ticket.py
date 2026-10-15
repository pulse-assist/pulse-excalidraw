# Тестовый билет плагина excalidraw на пробную доску (как выдаёт ядро при открытии в «Файлах»), с пометкой test.
import asyncio, os, sys
from motor.motor_asyncio import AsyncIOMotorClient
sys.path.insert(0, "/root/pulse/backend")
from app.models import file_tickets

async def main(path):
    db = AsyncIOMotorClient(os.environ["PULSE_MONGO_URL"])[os.environ["PULSE_MONGO_DB"]]
    t = await file_tickets.create_ticket(db, "excalidraw", path, "write", "0-0", issued_by="agent", run_id="xd-e2e-test")
    print(t)

asyncio.run(main(sys.argv[1]))
