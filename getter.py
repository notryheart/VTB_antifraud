from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI()

class TransferRequest(BaseModel):
    sender_account_id: int = Field(gt = 0)
    reciever_account_id: int = Field(gt = 0)
    amount: int = Field(gt = 0)
    currency: str = Field(pattern = r"^[A-Z]{3}$")

@app.post("/assess-transfer")
async def asess_transfer(transfer: TransferRequest):
    return{
        "message": "transfer recieved", "transfer": transfer.model_dump()
    }
    