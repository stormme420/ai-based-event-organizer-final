import os
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Generator

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from openai import AsyncOpenAI
from passlib.context import CryptContext
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import create_engine, String, Integer, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("event-manager")

APP_NAME = "Event Manager Pro"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'events.db'}")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
JWT_SECRET = os.getenv("JWT_SECRET", "default_jwt_secret_change_me_in_production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))
FRONTEND_ORIGINS = [x.strip() for x in os.getenv(
    "FRONTEND_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000,http://127.0.0.1:5500"
).split(",") if x.strip()]
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

# Major Abbottabad Locations, Hubs, and Venues Database
ABBOTTABAD_LOCATIONS = {
    "Nawansher / Murree Road": {
        "landmarks": ["Nawansher Chowk", "Ilyasi Masjid", "Murree Road corridor"],
        "venues": ["Modern Marquee", "Serene Marquee", "Cedar Hall"]
    },
    "Mansehra Road / Supply Area": {
        "landmarks": ["Supply Bazaar", "Gammon Mate", "Pine View Road"],
        "venues": ["Bandhan Flora Complex", "Millan Shadi Hall", "Riwayat Events"]
    },
    "Jinnahabad & Mandian": {
        "landmarks": ["Ayub Medical Complex (AMC)", "Mandian Main Market", "Comsats Road"],
        "venues": ["AURA Abbottabad", "Panorama Marriage Hall", "AWT Plaza Venues"]
    },
    "Kakul & PMA Road": {
        "landmarks": ["Pakistan Military Academy (PMA) Gate", "Kakul Heights", "Officers Mess Enclaves"],
        "venues": ["Alpine Hotel & Restaurant", "Army Officers Club / Mess (Protocol required)"]
    },
    "Shimla Hill & Upper City": {
        "landmarks": ["Shimla Pahari Park", "Lady Garden", "Club Road"],
        "venues": ["Shimla Wedding Hall", "Jalal Baba Auditorium", "Alpine Marquee"]
    },
    "Havelian & Bypass Region": {
        "landmarks": ["Havelian Interchange", "Hazara Motorway Link", "Sajikot Road"],
        "venues": ["Takht-e-Hazara Marriage Hall", "Mehfil Marriage Hall"]
    }
}

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_name: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(100))
    date: Mapped[str] = mapped_column(String(40))
    guests: Mapped[int] = mapped_column(Integer)
    location: Mapped[str] = mapped_column(String(255))
    budget: Mapped[float] = mapped_column(Float)
    venue_preference: Mapped[str] = mapped_column(String(255), default="Not sure")
    ai_plan: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    
class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    attendee_name: Mapped[str] = mapped_column(String(200))
    attendee_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    token: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    checked_in_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TicketCreate(BaseModel):
    attendee_name: str = Field(min_length=1, max_length=200)
    attendee_email: Optional[str] = Field(default=None, max_length=255)


class TicketCheckIn(BaseModel):
    token: str = Field(min_length=1, max_length=100)
Base.metadata.create_all(bind=engine)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

app = FastAPI(title=APP_NAME, version="2.0.0", description="AI-assisted event planning API")
app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "frontend"),
    name="static"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

class RegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class EventRequest(BaseModel):
    event_name: str = Field(min_length=2, max_length=200)
    event_type: str = Field(min_length=2, max_length=100)
    date: str = Field(min_length=4, max_length=40, description="ISO date preferred, e.g. 2026-12-25")
    guests: int = Field(gt=0, le=100000)
    location: str = Field(min_length=2, max_length=255)
    budget: float = Field(gt=0, le=1_000_000_000)
    venue_preference: Optional[str] = Field(default="Not sure", max_length=255)
    special_instructions: Optional[str] = Field(default="", max_length=5000)

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    event_context: Optional[str] = Field(default="", max_length=12000)

class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_name: str
    event_type: str
    date: str
    guests: int
    location: str
    budget: float
    venue_preference: str
    ai_plan: Optional[str] = None
    created_at: datetime

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES)
    return jwt.encode({"sub": subject, "exp": expire}, JWT_SECRET, algorithm=JWT_ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired access token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        email = payload.get("sub")
        if not email:
            raise credentials_error
    except JWTError:
        raise credentials_error
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_error
    return user

def get_ai_client() -> AsyncOpenAI:
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY is not configured.")
        raise HTTPException(
            status_code=503,
            detail="AI service is not configured. Add OPENROUTER_API_KEY to your environment or .env file."
        )
    return AsyncOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": os.getenv("APP_URL", "http://localhost:8000"),
            "X-Title": APP_NAME,
        },
        timeout=60.0,
        max_retries=2,
    )

async def ask_ai(system_prompt: str, user_prompt: str) -> str:
    """Send a prompt to OpenRouter and return the generated text."""
    client = get_ai_client()
    try:
        response = await client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise HTTPException(status_code=502, detail="The AI returned an empty response.")
        return content.strip()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OpenRouter request failed")
        raise HTTPException(
            status_code=502,
            detail="The AI service request failed. Check your OpenRouter model and API configuration.",
        ) from exc


async def health():
    return {"status": "ok", "app": APP_NAME, "ai_configured": bool(OPENROUTER_API_KEY)}

@app.get("/")
@app.get("/frontend")
async def serve_frontend():
    path = BASE_DIR / "frontend" / "index.html"
    if path.exists():
        return FileResponse(path)
    return {"message": "Event Manager Pro API is running", "docs": "/docs"}

@app.post("/api/auth/register", status_code=201)
async def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user = User(email=email, password_hash=get_password_hash(payload.password))
    db.add(user)
    db.commit()
    return {"success": True, "message": "Account created. You can now sign in."}

@app.post("/api/auth/token")
async def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    email = form.username.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password.", headers={"WWW-Authenticate": "Bearer"})
    return {"access_token": create_access_token(user.email), "token_type": "bearer", "expires_in": ACCESS_TOKEN_MINUTES * 60}

@app.get("/api/auth/me")
async def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "email": user.email}

@app.get("/api/abbottabad-locations")
async def get_abbottabad_locations():
    return {"success": True, "data": ABBOTTABAD_LOCATIONS}


@app.post("/create-event")
@app.post("/api/create-event")
async def create_event(
    event: EventRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from datetime import date

    # Provide local planning context without pretending estimates are verified.
    today = date.today()
    days_until_event = None
    try:
        event_date = date.fromisoformat(event.date[:10])
        days_until_event = (event_date - today).days
    except ValueError:
        event_date = None

    extra_abbottabad_info = ""
    if "abbottabad" in event.location.lower():
        extra_abbottabad_info = (
            "\nUse the supplied Abbottabad area guidance where relevant, "
            "but do not claim that a venue, price, or availability is verified. "
            f"Area references: {list(ABBOTTABAD_LOCATIONS.keys())}"
        )

    prompt = f"""
Create a practical, realistic event operations plan for Pakistan.

EVENT FACTS
- Name: {event.event_name}
- Type: {event.event_type}
- Date: {event.date}
- Guests: {event.guests}
- Location: {event.location}
- Total budget: PKR {event.budget:,.0f}
- Venue preference: {event.venue_preference}
- Special instructions: {event.special_instructions or "None"}
- Today's date: {today.isoformat()}
- Days until event: {days_until_event if days_until_event is not None else "Date could not be parsed"}

CORE PLANNING RULES
1. Treat the supplied event facts as authoritative. Do not invent additional
   ceremonies, religious rituals, guest preferences, or facilities.
2. Clearly distinguish known facts, assumptions, and items the organizer
   must confirm. Never claim you contacted a venue or checked availability.
3. Make recommendations specific to this event, guest count, location,
   date, and budget. Avoid generic filler.
4. Plan for the actual time remaining. Never assign tasks to a deadline
   that has already passed. Prioritize urgent bookings when time is short.
5. Use plausible planning estimates, not fabricated quotes. Label costs
   as estimates requiring confirmation from local vendors.
6. Do not assume a home can accommodate the guest count. Explain what
   must be checked: usable space, seating, weather cover, toilets,
   electricity, food-service space, access, and parking.
7. Estimate quantities where useful. Explain the basis, such as meals
   for the guest count plus a modest buffer, or the number of seats/tables.
   Mark quantities as planning assumptions, not exact requirements.
8. Consider staffing, setup and cleanup, delivery, waste, water, power,
   accessibility, guest flow, and weather where relevant.
9. Include a realistic event-day run sheet with setup, vendor arrival,
   guest arrival, main activities, service, cleanup, and responsible roles.
   Do not assume a Nikah or other ceremony unless the event details support it.
10. Include practical contingency plans for the most relevant risks.
11. Budget categories must be numeric PKR amounts. Include a contingency
    reserve and keep the combined amount within the stated total budget.
    Do not spend the same money in multiple categories. If the budget is
    unlikely to be sufficient, state the shortfall and the trade-offs.
12. Avoid made-up venue names, exact vendor prices, and unsupported claims
    about local prices or venue capacity. When local costs are uncertain,
    give reasonable ranges or state that quotes are needed.

{extra_abbottabad_info}

Return ONLY one valid JSON object. No Markdown fences or text outside JSON.
Use exactly these top-level keys and compatible field types:

{{
  "summary": {{
    "event_name": "string",
    "event_type": "string",
    "date": "YYYY-MM-DD",
    "guests": {event.guests},
    "location": "string",
    "total_budget": {event.budget},
    "estimated_cost": 0,
    "key_takeaways": ["specific insight", "specific insight"],
    "assumptions_to_confirm": ["important unknown"]
  }},
  "venue_recommendations": [
    {{
      "name": "Option or venue type",
      "area": "Area",
      "type": "Indoor, outdoor, home, or other",
      "estimated_price": "PKR estimate or range; quote required",
      "why_suitable": "Specific reason and limitations"
    }}
  ],
  "schedule": [
    {{
      "time": "time",
      "activity": "activity",
      "details": "practical instructions and responsible role"
    }}
  ],
  "catering_and_decor": {{
    "catering_items": ["menu item with quantity or serving assumption"],
    "decor_theme": "realistic theme",
    "decor_highlights": ["specific and feasible setup detail"],
    "operational_requirements": ["equipment, staffing, or logistics"]
  }},
  "budget_breakdown": [
    {{
      "category": "Specific cost category",
      "amount_pkr": 0,
      "percentage": "0%"
    }}
  ],
  "action_items": [
    {{
      "timeframe": "specific date or relative deadline",
      "task": "specific task and expected outcome"
    }}
  ],
  "risk_and_backup_plan": [
    {{
      "risk": "realistic risk",
      "prevention": "preventive action",
      "backup": "practical alternative"
    }}
  ]
}}

Before responding, check that the plan is consistent, the schedule is feasible,
the quantities fit the guest count, and all budget categories are considered.
"""

    system_prompt = """
You are a senior event operations planner experienced in planning events
in Pakistan. Be practical, precise, financially responsible, and honest
about uncertainty. Never invent verified prices, venue availability,
or event facts. Return valid JSON matching the requested schema only.
"""

    raw_response = await ask_ai(system_prompt, prompt)

    # Parse the response instead of saving unchecked AI text.
    try:
        start = raw_response.find("{")
        end = raw_response.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError("No JSON object found")
        plan = json.loads(raw_response[start:end])
    except (json.JSONDecodeError, ValueError) as exc:
        logger.exception("AI returned invalid event-plan JSON")
        raise HTTPException(
            status_code=502,
            detail="The AI returned an invalid plan. Please try generating it again."
        ) from exc

    # Keep the event details consistent with the user's actual submission.
    summary = plan.get("summary")
    if not isinstance(summary, dict):
        raise HTTPException(
            status_code=502,
            detail="The AI plan is missing its summary."
        )

    summary.update({
        "event_name": event.event_name,
        "event_type": event.event_type,
        "date": event.date,
        "guests": event.guests,
        "location": event.location,
        "total_budget": event.budget,
    })

    # Validate budget categories and make the allocation sum to the budget.
    budget_items = plan.get("budget_breakdown")
    if not isinstance(budget_items, list) or not budget_items:
        raise HTTPException(
            status_code=502,
            detail="The AI plan is missing its budget breakdown."
        )

    valid_items = []
    for item in budget_items:
        if not isinstance(item, dict):
            continue
        try:
            amount = float(item.get("amount_pkr", 0))
        except (TypeError, ValueError):
            continue
        if amount < 0:
            continue
        item["amount_pkr"] = amount
        valid_items.append(item)

    if not valid_items or sum(x["amount_pkr"] for x in valid_items) <= 0:
        raise HTTPException(
            status_code=502,
            detail="The AI returned an invalid budget breakdown. Please try again."
        )

    # Scale the AI's proposed allocations so the sum equals the user's budget.
    # This fixes arithmetic, but does not independently verify market prices.
    proposed_total = sum(x["amount_pkr"] for x in valid_items)
    rounded_total = 0

    for item in valid_items[:-1]:
        item["amount_pkr"] = round(
            event.budget * item["amount_pkr"] / proposed_total
        )
        rounded_total += item["amount_pkr"]

    valid_items[-1]["amount_pkr"] = round(event.budget) - rounded_total

    for item in valid_items:
        item["percentage"] = (
            f'{item["amount_pkr"] / event.budget * 100:.1f}%'
            if event.budget > 0 else "0%"
        )

    plan["budget_breakdown"] = valid_items
    summary["estimated_cost"] = sum(
        item["amount_pkr"] for item in valid_items
    )

    # Save the validated plan using the existing database model.
    event_data = event.model_dump(exclude={"special_instructions"})
    record = Event(
        owner_id=user.id,
        **event_data,
        ai_plan=json.dumps(plan, ensure_ascii=False)
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return {
        "success": True,
        "event": EventOut.model_validate(record),
        "ai_plan": json.dumps(plan, ensure_ascii=False)
    }


@app.get("/api/events", response_model=list[EventOut])
async def list_events(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Event).filter(Event.owner_id == user.id).order_by(Event.created_at.desc()).all()

@app.get("/api/events/{event_id}", response_model=EventOut)
async def get_event(event_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id, Event.owner_id == user.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")
    return event

@app.delete("/api/events/{event_id}", status_code=204)
async def delete_event(event_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id, Event.owner_id == user.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")
    db.delete(event)
    db.commit()
    return None

@app.post("/chat")
@app.post("/api/chat")
async def chat(request: ChatRequest, user: User = Depends(get_current_user)):

    system_prompt = """
You are Event Manager Pro, a practical AI event-planning assistant for Pakistan.

Your response style:
- Give the answer directly.
- Keep responses short, clean, and easy to scan.
- Use simple headings, bullet points, and numbered steps.
- Avoid large tables unless the user specifically asks for one.
- Do not repeat information the user already provided.
- Ask only for the most important missing information.
- Use PKR when discussing Pakistani event costs.
- Give estimates as ranges when exact prices are unknown.
- Never pretend to know an exact vendor's current price or availability.

For event cost-cutting questions:
1. Give the most important recommendation first.
2. List 5-7 practical ways to reduce cost.
3. Give approximate PKR savings where useful.
4. Mention the main trade-off or risk.
5. End with a short next step.

For vendor negotiation questions:
- Give a ready-to-copy WhatsApp/phone script.
- Keep the script natural and polite for the Pakistan market.
- If price cannot be reduced, suggest negotiating for extra services instead.
- Remind the user to confirm taxes, overtime, generator/AC, setup/cleanup, transport, service charges, cancellation terms, and payment schedule.

Preferred response structure:

### Quick Recommendation
Give the direct answer in 1-3 sentences.

### Best Options
- Option 1
- Option 2
- Option 3

### Vendor Script
Provide a short copy-paste message when relevant.

### Watch For
- Hidden charges
- Taxes/service charges
- Overtime
- Other important conditions

### Next Step
Give one clear action the user should take next.

Do not make answers unnecessarily long.
"""

    user_prompt = f"""
User email: {user.email}

Event context:
{request.event_context or "Not provided"}

User request:
{request.message}
"""

    reply = await ask_ai(system_prompt, user_prompt)

    return {"success": True, "reply": reply}

@app.post("/recommend-venues")
@app.post("/api/recommend-venues")
async def recommend_venues(event: EventRequest, user: User = Depends(get_current_user)):
    prompt = f"""Suggest venue selection guidance in Pakistan:
Location: {event.location}
Event Type: {event.event_type}
Guests: {event.guests}
Budget: PKR {event.budget:,.0f}
Preference: {event.venue_preference}

Provide options under Budget-friendly, Mid-range, and Premium/Hotel categories."""
    guidance = await ask_ai("You are a venue research assistant in Pakistan.", prompt)
    return {"success": True, "venue_guidance": guidance, "notice": "Pricing and availability must be verified directly with venues."}

@app.get("/api/config")
async def public_config():
    return {"app_name": APP_NAME, "registration_enabled": True, "ai_configured": bool(OPENROUTER_API_KEY)}

@app.post("/api/events/{event_id}/tickets")
async def create_ticket(
    event_id: int,
    payload: TicketCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(
        Event.id == event_id,
        Event.owner_id == user.id
    ).first()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")

    ticket = Ticket(
        event_id=event.id,
        owner_id=user.id,
        attendee_name=payload.attendee_name.strip(),
        attendee_email=payload.attendee_email,
        token=secrets.token_urlsafe(24),
    )

    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    return {
        "id": ticket.id,
        "event_id": ticket.event_id,
        "event_name": event.event_name,
        "attendee_name": ticket.attendee_name,
        "attendee_email": ticket.attendee_email,
        "token": ticket.token,
        "checked_in_at": ticket.checked_in_at,
    }


@app.get("/api/events/{event_id}/tickets")
async def list_tickets(
    event_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(
        Event.id == event_id,
        Event.owner_id == user.id
    ).first()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")

    tickets = db.query(Ticket).filter(
        Ticket.event_id == event_id,
        Ticket.owner_id == user.id
    ).order_by(Ticket.created_at.desc()).all()

    return [
        {
            "id": t.id,
            "event_id": t.event_id,
            "event_name": event.event_name,
            "attendee_name": t.attendee_name,
            "attendee_email": t.attendee_email,
            "token": t.token,
            "checked_in_at": t.checked_in_at,
        }
        for t in tickets
    ]


@app.post("/api/tickets/check-in")
async def check_in_ticket(
    payload: TicketCheckIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ticket = db.query(Ticket).filter(
        Ticket.token == payload.token,
        Ticket.owner_id == user.id
    ).first()

    if not ticket:
        raise HTTPException(
            status_code=404,
            detail="Invalid ticket or ticket not found."
        )

    if ticket.checked_in_at is not None:
        raise HTTPException(
            status_code=409,
            detail="This ticket has already been checked in."
        )

    ticket.checked_in_at = datetime.utcnow()
    db.commit()

    return {
        "success": True,
        "message": "Ticket checked in successfully.",
        "attendee_name": ticket.attendee_name,
        "event_id": ticket.event_id,
        "checked_in_at": ticket.checked_in_at,
    }