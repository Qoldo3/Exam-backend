"""Seed script: recreate all tables, then add exams + questions + operator accounts."""
import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, Base, engine
from app.models.exam import Exam, Question
from app.models.user import User
from app.seed import EXAMS


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        for spec in EXAMS:
            exam = Exam(
                title=spec["title"],
                description=spec["description"],
                duration_secs=spec["duration_secs"],
                price=spec["price"],
            )
            db.add(exam)
            await db.flush()
            for i, (body, a, b, c, d, correct) in enumerate(spec["questions"]):
                db.add(
                    Question(
                        exam_id=exam.id,
                        body=body,
                        option_a=a,
                        option_b=b,
                        option_c=c,
                        option_d=d,
                        correct_opt=correct,
                        order_num=i,
                    )
                )

        # production seed: only operator accounts — no demo/student users
        db.add(User(full_name="مدیر ارشد سامانه", national_id="0012345679", phone="09120000000", role="superadmin"))
        db.add(User(full_name="مدیر آزمون‌ها", national_id="0034567895", phone="09120000007", role="exam_manager"))
        db.add(User(full_name="مسئول مالی", national_id="0045678911", phone="09120000008", role="finance"))
        await db.commit()

        exams = (await db.execute(select(Exam))).scalars().all()
        users = (await db.execute(select(User))).scalars().all()
        print(f"Seeded {len(exams)} exams, {len(users)} users")


if __name__ == "__main__":
    asyncio.run(main())
