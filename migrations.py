from app import db, app

with app.app_context():
    db.engine.execute('CREATE INDEX IF NOT EXISTS idx_transaction_date ON transaction(date);')
    db.engine.execute('CREATE INDEX IF NOT EXISTS idx_transaction_category ON transaction(category_id);')