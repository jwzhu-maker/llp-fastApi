import random

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, ForeignKey, select
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from databases import Database
from pydantic import BaseModel

DATABASE_URL = "sqlite:///./identifier.sqlite"
database = Database(DATABASE_URL)

metadata = MetaData()

sessions_table = Table(
    "sessions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String),
    Column("progress", Integer),
    Column("expected_number_of_participants", Integer),
    Column("owner_id", Integer, ForeignKey("users.id")),
    Column("status", String),
    Column("result", String, ForeignKey("restaurants.name"))
)

users_table = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, unique=True)
)

restaurants_table = Table(
    "restaurants",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("session_id", Integer, ForeignKey("sessions.id")),
    Column("name", String)
)

user_session_restaurants_table = Table(
    "user_session_restaurants",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id")),
    Column("session_id", Integer, ForeignKey("sessions.id")),
    Column("restaurant_id", Integer, ForeignKey("restaurants.id"))
)

# Declare your models
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)


class Sessions(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    progress = Column(Integer)
    expected_number_of_participants = Column(Integer)
    owner_id = Column(Integer, ForeignKey('users.id'))  # Foreign key to User

    owner = relationship("User")  # This establishes the relationship
    status = Column(String, index=True)  # To indicate, a session is open or closed
    result = Column(String, ForeignKey('restaurant.name'))  # the result of the session, i.e., the restaurant


class Restaurant(Base):
    __tablename__ = "restaurants"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)


class UserSessionRestaurant(Base):
    __tablename__ = "user_session_restaurants"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), index=True)
    session_id = Column(Integer, ForeignKey('sessions_id'), index=True)
    restaurant_id = Column(Integer, ForeignKey('restaurants.id'), index=True)


# Create the database tables
engine = create_engine(DATABASE_URL)
metadata.create_all(engine)

# Session local for SQLAlchemy
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# Define a list of origins that should be permitted to make cross-origin requests
origins = [
    "http://localhost:3000",  # Allow frontend origin
    "http://localhost",  # Depending on your needs, you can be more restrictive
]

# Add CORSMiddleware to the application instance
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=False,  # Important: set to False if you're using wildcard origins
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)


@app.on_event("startup")
async def startup():
    await database.connect()


@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/login/")
async def login(login_request: LoginRequest):
    # no need to verify password for now, check if user exists
    query = users_table.select().where(users_table.c.name == login_request.username)
    user = await database.fetch_one(query)
    if not user:
        # user does not exist, create it
        query = users_table.insert().values(name=login_request.username)
        last_record_id = await database.execute(query)
        return {"user_id": last_record_id, "username": login_request.username}

    return {"user_id": user["id"], "username": user["name"]}


class CreateSessionRequest(BaseModel):
    name: str
    owner_name: str
    session_participants: int


@app.post("/create-session/")
async def create_session(session_request: CreateSessionRequest):
    print(session_request)
    # First, find the user by owner_name to get the owner_id
    query_user = User.__table__.select().where(User.name == session_request.owner_name)
    user = await database.fetch_one(query_user)

    if not user:
        query_user = users_table.insert().values(name=session_request.owner_name)
        last_record_id = await database.execute(query_user)
        owner_id = last_record_id
    else:
        owner_id = user["id"]

    # Now, create the session with the owner_id
    query_session = sessions_table.insert().values(name=session_request.name,
                                                   owner_id=owner_id,
                                                   expected_number_of_participants=session_request.session_participants,
                                                   status="open",
                                                   progress=0)
    last_record_id = await database.execute(query_session)
    return {**{"session_id": last_record_id, "name": session_request.name, "owner_id": owner_id}}


@app.put("/session/{session_id}/end")
async def end_session(session_id: int):
    # First, find the session
    query_session = sessions_table.select().where(sessions_table.c.id == session_id)
    session = await database.fetch_one(query_session)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Now, update the session status to closed
    query_session = (sessions_table.update().where(sessions_table.c.id == session_id)
                     .values(status="closed"))
    await database.execute(query_session)

    # Now, randomly select a restaurant from the submissions
    query = user_session_restaurants_table.select().where(
        user_session_restaurants_table.c.session_id == session_id
    )
    submissions = await database.fetch_all(query)
    random_result = random.choice(submissions)

    # get the restaurant name from the restaurant_id
    query = restaurants_table.select().where(restaurants_table.c.id == random_result["restaurant_id"])
    restaurant_selected = await database.fetch_one(query)

    # Update the sessions table the result
    query = sessions_table.update().where(sessions_table.c.id == session_id)
    query = query.values(result=restaurant_selected["name"])
    await database.execute(query)

    return {"message": "Session ended successfully", "result": restaurant_selected["name"]}


@app.get("/check-submission/{session_id}/{username}")
async def check_submission(session_id: int, username: str):
    """
    Check if the user has submitted a restaurant for the given session.

    :param session_id: The ID of the session.
    :type session_id: int
    :param username: The ID of the user.
    :type username: str
    :return: A dictionary indicating if the user has submitted a restaurant and the restaurant name if submitted.
    :rtype: dict

    """
    # First, find the user ID based on the username
    query_user = User.__table__.select().where(User.name == username)
    user = await database.fetch_one(query_user)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user_id = user["id"]

    query = user_session_restaurants_table.select().where(
        (user_session_restaurants_table.c.session_id == session_id) &
        (user_session_restaurants_table.c.user_id == user_id)
    )
    submission = await database.fetch_one(query)
    if submission:
        query = restaurants_table.select().where(restaurants_table.c.id == submission["restaurant_id"])
        restaurant = await database.fetch_one(query)
        return {"submitted": True, "restaurantName": restaurant["name"]}
    return {"submitted": False}


@app.get("/sessions/")
async def get_sessions():
    query = sessions_table.select()
    return await database.fetch_all(query)


@app.get("/session/{session_id}/submissions")
async def get_session_submissions(session_id: int):
    # Constructing the SQL query
    query = select([
        users_table.c.name.label("user_name"),
        restaurants_table.c.name.label("restaurant_name")
    ]).select_from(
        user_session_restaurants_table
        .join(users_table, user_session_restaurants_table.c.user_id == users_table.c.id)
        .join(restaurants_table, user_session_restaurants_table.c.restaurant_id == restaurants_table.c.id)
    ).where(user_session_restaurants_table.c.session_id == session_id)

    data = await database.fetch_all(query)

    # Formatting the response
    submissions = [{"user": row["user_name"], "restaurant": row["restaurant_name"]} for row in data]
    return submissions


@app.get("/session/{session_id}/owner/")
async def get_session_owner(session_id: int):
    # Constructing a JOIN query to fetch session and owner details
    query = select([
        sessions_table,
        users_table.c.name.label("owner_name")
    ]).select_from(
        sessions_table.join(users_table, sessions_table.c.owner_id == users_table.c.id)
    ).where(sessions_table.c.id == session_id)

    session_with_owner = await database.fetch_one(query)

    if not session_with_owner:
        raise HTTPException(status_code=404, detail="Session not found")

    # Convert the result to dict if needed
    session_data = dict(session_with_owner)
    return session_data


class RestaurantSubmission(BaseModel):
    session_id: int
    restaurant_name: str
    user_name: str


@app.post("/submit-restaurant/")
async def submit_restaurant(submission: RestaurantSubmission):
    # First, validate the session ID
    query_session = sessions_table.select().where(sessions_table.c.id == submission.session_id)
    session = await database.fetch_one(query_session)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Next, get the user ID from the username
    query_user = users_table.select().where(users_table.c.name == submission.user_name)
    user = await database.fetch_one(query_user)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Next, check if the restaurant already exists, and if not, create it
    query_restaurant = restaurants_table.select().where(restaurants_table.c.name == submission.restaurant_name)
    restaurant = await database.fetch_one(query_restaurant)

    if not restaurant:
        query_restaurant = restaurants_table.insert().values(name=submission.restaurant_name)
        last_record_id = await database.execute(query_restaurant)
        restaurant = {"id": last_record_id}

    # Add the restaurant submission to the database
    query_restaurant = user_session_restaurants_table.insert().values(
        user_id=user["id"],
        session_id=submission.session_id,
        restaurant_id=restaurant["id"],
    )
    await database.execute(query_restaurant)

    # Update session progress
    # 1. Select the total number of submissions for the session
    query = user_session_restaurants_table.select().where(
        user_session_restaurants_table.c.session_id == submission.session_id
    )
    submissions = await database.fetch_all(query)

    # 2. Update the session progress
    participants = session["expected_number_of_participants"]
    if participants > 0:
        progress_value = round(len(submissions) * 100 / participants)
    else:
        progress_value = 0

    query_session = (sessions_table.update().where(sessions_table.c.id == submission.session_id)
                     .values(progress=progress_value))
    await database.execute(query_session)

    return {"message": "Restaurant submitted successfully"}


# user APIs
@app.get("/user/{user_name}")
async def get_user(user_name: str):
    query = users_table.select().where(users_table.c.name == user_name)
    user = await database.fetch_one(query)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


if __name__ == "__main__":
    uvicorn.run(app, host='0.0.0.0', port=8000)
