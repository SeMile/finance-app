from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy import func
from io import BytesIO
import pandas as pd

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///finance.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Модели
class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(10), nullable=False)  # 'income' или 'expense'

class CreditCard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    credit_limit = db.Column(db.Float, nullable=False)
    balance = db.Column(db.Float, nullable=False)
    is_active = db.Column(db.Boolean, default=True)

class BudgetPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    month = db.Column(db.String(7), nullable=False)  # Формат: 'YYYY-MM'
    amount = db.Column(db.Float, nullable=False)
    category = db.relationship('Category', backref='budget_plans')

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), nullable=False)  # 'income', 'expense', 'credit_refill'
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False)
    comment = db.Column(db.String(200))
    source = db.Column(db.String(100))  # 'personal' или 'credit_<card_id>'
    category = db.relationship('Category', backref='transactions')

# Создаем таблицы
with app.app_context():
    db.create_all()

# Фильтр для форматирования валюты
@app.template_filter('format_currency')
def format_currency(value):
    return f"{float(value):,.2f} ₽".replace(",", " ")

# Контекстный процессор
@app.context_processor
def utility_processor():
    def get_credit_card(card_id):
        return CreditCard.query.get(card_id)
    return dict(get_credit_card=get_credit_card)

# Вспомогательные функции
def calculate_personal_balance():
    income = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.type == 'income',
        Transaction.source == 'personal'
    ).scalar() or 0
    
    expense = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.type == 'expense',
        Transaction.source == 'personal'
    ).scalar() or 0
    
    return income - expense

# Маршруты
@app.route('/')
def dashboard():
    current_month = datetime.now().strftime('%Y-%m')
    
    # Данные для плашек
    total_income = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.type == 'income',
        func.strftime('%Y-%m', Transaction.date) == current_month
    ).scalar() or 0
    
    total_expense = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.type == 'expense',
        func.strftime('%Y-%m', Transaction.date) == current_month
    ).scalar() or 0
    
    # Данные для таблицы расходов
    expense_categories = Category.query.filter_by(type='expense').all()
    budget_plans = {
        plan.category_id: plan.amount 
        for plan in BudgetPlan.query.filter_by(month=current_month)
    }
    
    actual_expenses = {}
    for cat in expense_categories:
        total = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.category_id == cat.id,
            Transaction.type == 'expense',
            func.strftime('%Y-%m', Transaction.date) == current_month
        ).scalar() or 0
        actual_expenses[cat.id] = total
    
    total_planned = sum(budget_plans.values())
    total_actual = sum(actual_expenses.values())
    
    # Данные по кредитным картам
    credit_cards = CreditCard.query.filter_by(is_active=True).all()
    total_credit = sum(card.credit_limit for card in credit_cards)
    available_credit = sum(card.balance for card in credit_cards)
    
    return render_template(
        'dashboard.html',
        total_income=total_income,
        total_expense=total_expense,
        expense_categories=expense_categories,
        budget_plans=budget_plans,
        actual_expenses=actual_expenses,
        total_planned=total_planned,
        total_actual=total_actual,
        credit_cards=credit_cards,
        total_credit=total_credit,
        available_credit=available_credit,
        current_month=current_month,
        now=datetime.now()
    )

@app.route('/transactions', methods=['GET', 'POST'])
def transactions():
    if request.method == 'POST':
        try:
            operation_type = request.form['type']
            amount = float(request.form['amount'])
            date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
            comment = request.form.get('comment', '')
            
            if operation_type == 'credit_refill':
                # Пополнение кредитной карты
                card_id = int(request.form['credit_card'])
                card.balance = min(card.credit_limit, card.balance + amount)
                card = CreditCard.query.get_or_404(card_id)
                
                personal_balance = calculate_personal_balance()
                if personal_balance < amount:
                    raise ValueError("Недостаточно средств для пополнения")
                
                # Создаем операцию списания
                transaction = Transaction(
                    type='expense',
                    category_id=None,
                    amount=amount,
                    date=date,
                    comment=f"Пополнение кредитной карты {card.name}",
                    source='personal'
                )
                db.session.add(transaction)
                
                # Пополняем кредитную карту
                card.balance = min(card.credit_limit, card.balance + amount)
                
            else:
                # Обычная операция
                category_id = request.form['category_id']
                source = request.form['source']
                category = Category.query.get_or_404(category_id)
                
                if category.type == 'expense' and source.startswith('credit_'):
                    card_id = int(source.split('_')[1])
                    card = CreditCard.query.get_or_404(card_id)
                    if card.balance < amount:
                        raise ValueError("Недостаточно средств на кредитной карте")
                    card.balance -= amount
                
                transaction = Transaction(
                    type=category.type if operation_type != 'credit_refill' else operation_type,
                    category_id=category_id,
                    amount=amount,
                    date=date,
                    comment=comment,
                    source=source
                )
                db.session.add(transaction)
            
            db.session.commit()
            
        except ValueError as e:
            db.session.rollback()
            print(f"Ошибка: {str(e)}")
        except Exception as e:
            db.session.rollback()
            print(f"Ошибка при добавлении операции: {str(e)}")
            
        return redirect(url_for('transactions'))
    
    transactions = Transaction.query.order_by(Transaction.date.desc()).all()
    categories = Category.query.order_by(Category.name).all()
    credit_cards = CreditCard.query.filter_by(is_active=True).all()
    
    return render_template(
        'transactions.html',
        transactions=transactions,
        categories=categories,
        credit_cards=credit_cards,
        now=datetime.now()
    )

@app.route('/categories', methods=['GET', 'POST'])
def categories():
    if request.method == 'POST':
        name = request.form['name']
        type_ = request.form['type']
        new_category = Category(name=name, type=type_)
        db.session.add(new_category)
        db.session.commit()
        return redirect(url_for('categories'))
    
    categories = Category.query.all()
    return render_template('categories.html', categories=categories)

@app.route('/delete_category/<int:id>')
def delete_category(id):
    category = Category.query.get_or_404(id)
    
    # Проверяем, есть ли связанные операции
    if Transaction.query.filter_by(category_id=id).count() > 0:
        return redirect(url_for('categories'))
    
    # Проверяем, есть ли связанные планы бюджета
    if BudgetPlan.query.filter_by(category_id=id).count() > 0:
        return redirect(url_for('categories'))
    
    db.session.delete(category)
    db.session.commit()
    return redirect(url_for('categories'))

@app.route('/budget', methods=['GET', 'POST'])
def budget():
    if request.method == 'POST':
        category_id = request.form['category_id']
        month = request.form['month']
        amount = float(request.form['amount'])
        
        # Проверяем, есть ли уже план для этой категории и месяца
        existing_plan = BudgetPlan.query.filter_by(
            category_id=category_id,
            month=month
        ).first()
        
        if existing_plan:
            existing_plan.amount = amount
        else:
            new_plan = BudgetPlan(
                category_id=category_id,
                month=month,
                amount=amount
            )
            db.session.add(new_plan)
        
        db.session.commit()
        return redirect(url_for('budget'))
    
    plans = BudgetPlan.query.order_by(BudgetPlan.month).all()
    categories = Category.query.filter_by(type='expense').all()
    return render_template('budget.html', plans=plans, categories=categories)

@app.route('/delete_plan/<int:id>')
def delete_plan(id):
    plan = BudgetPlan.query.get_or_404(id)
    db.session.delete(plan)
    db.session.commit()
    return redirect(url_for('budget'))

@app.route('/credit_cards', methods=['GET', 'POST'])
def credit_cards():
    if request.method == 'POST':
        try:
            name = request.form['name']
            limit = float(request.form['limit'])
            
            new_card = CreditCard(
                name=name,
                credit_limit=limit,
                balance=limit,
                is_active=True
            )
            
            db.session.add(new_card)
            db.session.commit()
        except ValueError:
            db.session.rollback()
        except Exception:
            db.session.rollback()
            
        return redirect(url_for('credit_cards'))
    
    cards = CreditCard.query.order_by(CreditCard.name).all()
    return render_template('credit_cards.html', cards=cards)

@app.route('/toggle_card/<int:card_id>')
def toggle_card(card_id):
    card = CreditCard.query.get_or_404(card_id)
    card.is_active = not card.is_active
    db.session.commit()
    return redirect(url_for('credit_cards'))

@app.route('/export/excel')
def export_excel():
    transactions = Transaction.query.all()
    data = [{
        'Дата': t.date.strftime('%Y-%m-%d'),
        'Тип': 'Доход' if t.type == 'income' else 'Расход',
        'Категория': t.category.name if t.category else '-',
        'Сумма': t.amount,
        'Источник': 'Собственные средства' if t.source == 'personal' else 
                   f"Кредитная карта: {CreditCard.query.get(int(t.source.split('_')[1])).name}",
        'Комментарий': t.comment
    } for t in transactions]

    df = pd.DataFrame(data)
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='transactions.xlsx'
    )

if __name__ == '__main__':
    app.run(debug=True)