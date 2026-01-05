from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Item(db.Model):
    """
    SKU is the primary key and auto-increments like your spreadsheet.
    """
    __tablename__ = "items"

    sku = db.Column(db.Integer, primary_key=True, autoincrement=True)  # SKU
    item_name = db.Column(db.String(255), nullable=False)

    category = db.Column(db.String(120), nullable=True)
    sub_category = db.Column(db.String(120), nullable=True)
    platform = db.Column(db.String(120), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    cog = db.Column(db.Float, nullable=True)          # cost of goods
    sale_price = db.Column(db.Float, nullable=True)
    ad_fee = db.Column(db.Float, nullable=True)
    ebay_fee = db.Column(db.Float, nullable=True)
    shipping = db.Column(db.Float, nullable=True)
    buyer_paid_amount = db.Column(db.Float, nullable=True)

    date_listed = db.Column(db.Date, nullable=True)
    date_sold = db.Column(db.Date, nullable=True)
    sold = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    images = db.relationship("ItemImage", backref="item", cascade="all, delete-orphan")

    def _n(self, v):
        """Treat None as 0 for math, like spreadsheets."""
        return float(v or 0.0)

    @property
    def net_cost(self) -> float:
        # Net Cost = COG + Ad Fee + eBay Fee + Shipping
        return self._n(self.cog) + self._n(self.ad_fee) + self._n(self.ebay_fee) + self._n(self.shipping)

    @property
    def gross_profit(self) -> float:
        # Gross Profit = Buyer Paid Amount - Net Cost (if buyer amount blank -> negative net cost)
        return self._n(self.buyer_paid_amount) - self.net_cost


class ItemImage(db.Model):
    __tablename__ = "item_images"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    item_sku = db.Column(db.Integer, db.ForeignKey("items.sku"), nullable=False)
    filename = db.Column(db.String(500), nullable=False)  # stored filename on disk
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)