import os
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from werkzeug.utils import secure_filename

from models import db, Item, ItemImage

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_float(value: str):
    if value is None:
        return None
    v = value.strip()
    if v == "":
        return None
    v = v.replace("$", "").replace(",", "")
    try:
        return float(v)
    except ValueError:
        return None


def parse_date(value: str):
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return None


def get_distinct_values(model, column):
    """
    Returns distinct non-empty values for a column from the DB,
    used to populate datalist dropdown suggestions.
    """
    rows = db.session.query(column).distinct().filter(column.isnot(None)).order_by(column).all()
    values = []
    for r in rows:
        if not r or r[0] is None:
            continue
        s = str(r[0]).strip()
        if s:
            values.append(s)
    return values


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

    # DB path (Docker compose can override this)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "SQLALCHEMY_DATABASE_URI",
        "sqlite:///ebay_tracker.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Uploads folder (Docker compose can override this)
    default_uploads_dir = Path(app.root_path) / "uploads" / "items"
    upload_folder = os.environ.get("UPLOAD_FOLDER", str(default_uploads_dir))
    app.config["UPLOAD_FOLDER"] = upload_folder

    # Ensure upload folder exists (works for /data/uploads in Docker too)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)

    with app.app_context():
        db.create_all()

    @app.route("/uploads/items/<path:filename>")
    def uploaded_file(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    @app.route("/")
    def index():
        sold_filter = request.args.get("sold", "")  # "", "Y", "N"
        platform = request.args.get("platform", "").strip()
        category = request.args.get("category", "").strip()
        q = request.args.get("q", "").strip()

        query = Item.query

        if sold_filter == "Y":
            query = query.filter(Item.sold.is_(True))
        elif sold_filter == "N":
            query = query.filter(Item.sold.is_(False))

        if platform:
            query = query.filter(Item.platform == platform)
        if category:
            query = query.filter(Item.category == category)
        if q:
            like = f"%{q}%"
            query = query.filter(
                (Item.item_name.ilike(like)) |
                (Item.notes.ilike(like)) |
                (Item.sub_category.ilike(like)) |
                (Item.category.ilike(like))
            )

        items = query.order_by(Item.sku.desc()).all()

        platforms = get_distinct_values(Item, Item.platform)
        categories = get_distinct_values(Item, Item.category)

        return render_template(
            "index.html",
            items=items,
            platforms=platforms,
            categories=categories,
            sold_filter=sold_filter,
            platform_filter=platform,
            category_filter=category,
            q=q,
        )

    @app.route("/item/new", methods=["GET", "POST"])
    def item_new():
        if request.method == "POST":
            item = Item(
                item_name=request.form.get("item_name", "").strip(),
                category=request.form.get("category", "").strip() or None,
                sub_category=request.form.get("sub_category", "").strip() or None,
                platform=request.form.get("platform", "").strip() or None,
                notes=request.form.get("notes", "").strip() or None,
                cog=parse_float(request.form.get("cog")),
                sale_price=parse_float(request.form.get("sale_price")),
                ad_fee=parse_float(request.form.get("ad_fee")),
                ebay_fee=parse_float(request.form.get("ebay_fee")),
                shipping=parse_float(request.form.get("shipping")),
                buyer_paid_amount=parse_float(request.form.get("buyer_paid_amount")),
                date_listed=parse_date(request.form.get("date_listed")),
                date_sold=parse_date(request.form.get("date_sold")),
                sold=(request.form.get("sold") == "Y"),
            )

            if not item.item_name:
                flash("Item Name is required.", "error")
                categories = get_distinct_values(Item, Item.category)
                sub_categories = get_distinct_values(Item, Item.sub_category)
                platforms = get_distinct_values(Item, Item.platform)
                return render_template(
                    "item_new.html",
                    categories=categories,
                    sub_categories=sub_categories,
                    platforms=platforms,
                )

            db.session.add(item)
            db.session.commit()  # assigns SKU

            # Handle uploads (multiple)
            files = request.files.getlist("photos")
            for f in files:
                if not f or f.filename == "":
                    continue
                if not allowed_file(f.filename):
                    flash(f"Skipped {f.filename}: unsupported file type.", "warning")
                    continue

                safe = secure_filename(f.filename)
                ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
                ext = safe.rsplit(".", 1)[1].lower()
                stored_name = f"SKU{item.sku}_{ts}.{ext}"
                f.save(os.path.join(app.config["UPLOAD_FOLDER"], stored_name))
                db.session.add(ItemImage(item_sku=item.sku, filename=stored_name))

            db.session.commit()

            flash(f"Created item SKU #{item.sku}.", "success")
            return redirect(url_for("item_detail", sku=item.sku))

        # GET
        categories = get_distinct_values(Item, Item.category)
        sub_categories = get_distinct_values(Item, Item.sub_category)
        platforms = get_distinct_values(Item, Item.platform)
        return render_template(
            "item_new.html",
            categories=categories,
            sub_categories=sub_categories,
            platforms=platforms,
        )

    @app.route("/item/<int:sku>")
    def item_detail(sku: int):
        item = Item.query.get_or_404(sku)
        return render_template("item_detail.html", item=item)

    @app.route("/item/<int:sku>/edit", methods=["GET", "POST"])
    def item_edit(sku: int):
        item = Item.query.get_or_404(sku)

        if request.method == "POST":
            item.item_name = request.form.get("item_name", "").strip()
            item.category = request.form.get("category", "").strip() or None
            item.sub_category = request.form.get("sub_category", "").strip() or None
            item.platform = request.form.get("platform", "").strip() or None
            item.notes = request.form.get("notes", "").strip() or None

            item.cog = parse_float(request.form.get("cog"))
            item.sale_price = parse_float(request.form.get("sale_price"))
            item.ad_fee = parse_float(request.form.get("ad_fee"))
            item.ebay_fee = parse_float(request.form.get("ebay_fee"))
            item.shipping = parse_float(request.form.get("shipping"))
            item.buyer_paid_amount = parse_float(request.form.get("buyer_paid_amount"))

            item.date_listed = parse_date(request.form.get("date_listed"))
            item.date_sold = parse_date(request.form.get("date_sold"))
            item.sold = (request.form.get("sold") == "Y")

            if not item.item_name:
                flash("Item Name is required.", "error")
                categories = get_distinct_values(Item, Item.category)
                sub_categories = get_distinct_values(Item, Item.sub_category)
                platforms = get_distinct_values(Item, Item.platform)
                return render_template(
                    "item_edit.html",
                    item=item,
                    categories=categories,
                    sub_categories=sub_categories,
                    platforms=platforms,
                )

            # Add new photos if uploaded
            files = request.files.getlist("photos")
            for f in files:
                if not f or f.filename == "":
                    continue
                if not allowed_file(f.filename):
                    flash(f"Skipped {f.filename}: unsupported file type.", "warning")
                    continue

                safe = secure_filename(f.filename)
                ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
                ext = safe.rsplit(".", 1)[1].lower()
                stored_name = f"SKU{item.sku}_{ts}.{ext}"
                f.save(os.path.join(app.config["UPLOAD_FOLDER"], stored_name))
                db.session.add(ItemImage(item_sku=item.sku, filename=stored_name))

            db.session.commit()
            flash(f"Updated SKU #{item.sku}.", "success")
            return redirect(url_for("item_detail", sku=item.sku))

        # GET
        categories = get_distinct_values(Item, Item.category)
        sub_categories = get_distinct_values(Item, Item.sub_category)
        platforms = get_distinct_values(Item, Item.platform)
        return render_template(
            "item_edit.html",
            item=item,
            categories=categories,
            sub_categories=sub_categories,
            platforms=platforms,
        )

    @app.route("/image/<int:image_id>/delete", methods=["POST"])
    def delete_image(image_id: int):
        img = ItemImage.query.get_or_404(image_id)
        sku = img.item_sku

        path = os.path.join(app.config["UPLOAD_FOLDER"], img.filename)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

        db.session.delete(img)
        db.session.commit()
        flash("Image deleted.", "success")
        return redirect(url_for("item_detail", sku=sku))

    @app.route("/item/<int:sku>/delete", methods=["POST"])
    def item_delete(sku: int):
        item = Item.query.get_or_404(sku)

        for img in item.images:
            path = os.path.join(app.config["UPLOAD_FOLDER"], img.filename)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

        db.session.delete(item)
        db.session.commit()
        flash(f"Deleted SKU #{sku}.", "success")
        return redirect(url_for("index"))

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5055, debug=True)
