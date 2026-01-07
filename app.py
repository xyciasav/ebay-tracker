import os
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy import func, case
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps
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
    rows = db.session.query(column).distinct().filter(column.isnot(None)).order_by(column).all()
    values = []
    for r in rows:
        if not r or r[0] is None:
            continue
        s = str(r[0]).strip()
        if s:
            values.append(s)
    return values


def process_image(path: str, max_size: int = 1600):
    """
    Shrinks huge phone photos and fixes sideways rotation using EXIF.
    Overwrites the file at 'path' with an optimized version.
    """
    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)  # auto-rotate correctly

        # Resize (keeps aspect ratio). Longest side becomes <= max_size.
        img.thumbnail((max_size, max_size))

        # Convert to a safe mode for saving
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        img.save(path, optimize=True, quality=85)

    except Exception as e:
        print(f"Image processing failed for {path}: {e}")


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

    # DB URI can be overridden in Docker
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "SQLALCHEMY_DATABASE_URI",
        "sqlite:///ebay_tracker.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Upload folder can be overridden in Docker
    default_uploads_dir = Path(app.root_path) / "uploads" / "items"
    upload_folder = os.environ.get("UPLOAD_FOLDER", str(default_uploads_dir))
    app.config["UPLOAD_FOLDER"] = upload_folder

    # Ensure upload folder exists
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
        source_locations = get_distinct_values(Item, Item.source_location)

        return render_template(
            "index.html",
            items=items,
            platforms=platforms,
            categories=categories,
            source_locations=source_locations,  # ✅ NEW
            sold_filter=sold_filter,
            platform_filter=platform,
            category_filter=category,
            q=q,
        )

    @app.route("/reports")
    def reports():
        range_key = (request.args.get("range") or "all").strip().lower()
        start_s = (request.args.get("start") or "").strip()
        end_s = (request.args.get("end") or "").strip()

        today = datetime.utcnow().date()

        # Resolve range -> start/end dates (inclusive)
        start_date = None
        end_date = None

        if range_key == "30d":
            start_date = today - timedelta(days=30)
            end_date = today
        elif range_key == "90d":
            start_date = today - timedelta(days=90)
            end_date = today
        elif range_key == "this_month":
            start_date = today.replace(day=1)
            end_date = today
        elif range_key == "last_month":
            first_this_month = today.replace(day=1)
            last_month_end = first_this_month - timedelta(days=1)
            start_date = last_month_end.replace(day=1)
            end_date = last_month_end
        elif range_key == "this_year":
            start_date = today.replace(month=1, day=1)
            end_date = today
        elif range_key == "last_year":
            start_date = today.replace(year=today.year - 1, month=1, day=1)
            end_date = today.replace(year=today.year - 1, month=12, day=31)
        elif range_key == "custom":
            start_date = parse_date(start_s)
            end_date = parse_date(end_s)
            if start_date and end_date and start_date > end_date:
                start_date, end_date = end_date, start_date
        else:
            range_key = "all"

        # Build sold-date filters (used for sold/profit metrics)
        sold_date_filters = []
        if start_date:
            sold_date_filters.append(Item.date_sold.isnot(None))
            sold_date_filters.append(Item.date_sold >= start_date)
        if end_date:
            sold_date_filters.append(Item.date_sold.isnot(None))
            sold_date_filters.append(Item.date_sold <= end_date)

        # Helpers
        def nz(col):
            return func.coalesce(col, 0.0)

        profit_expr = (
            nz(Item.buyer_paid_amount)
            - (nz(Item.cog) + nz(Item.shipping) + nz(Item.ad_fee) + nz(Item.ebay_fee))
        )

        days_to_sell_expr = case(
            (
                (Item.date_listed.isnot(None)) & (Item.date_sold.isnot(None)),
                func.julianday(Item.date_sold) - func.julianday(Item.date_listed),
            ),
            else_=None,
        )

        category_col = func.coalesce(Item.category, "Uncategorized")

        # -----------------------------
        # KPIs
        # -----------------------------
        total_items = Item.query.count()

        sold_items_q = Item.query.filter(Item.sold.is_(True))
        if sold_date_filters:
            sold_items_q = sold_items_q.filter(*sold_date_filters)
        sold_items = sold_items_q.count()

        sold_rate_pct = (sold_items / total_items * 100.0) if total_items else 0.0

        total_profit_q = (
            db.session.query(func.coalesce(func.sum(profit_expr), 0.0))
            .filter(Item.sold.is_(True))
        )
        if sold_date_filters:
            total_profit_q = total_profit_q.filter(*sold_date_filters)

        total_profit = float(total_profit_q.scalar() or 0.0)
        avg_profit_per_sold = (total_profit / sold_items) if sold_items else 0.0

        avg_days_to_sell_q = (
            db.session.query(func.avg(days_to_sell_expr))
            .filter(Item.sold.is_(True))
        )
        if sold_date_filters:
            avg_days_to_sell_q = avg_days_to_sell_q.filter(*sold_date_filters)

        avg_days_to_sell = avg_days_to_sell_q.scalar()
        avg_days_to_sell = float(avg_days_to_sell) if avg_days_to_sell is not None else 0.0

        # -----------------------------
        # Category counts (all items, current inventory)
        # -----------------------------
        sold_count_all = func.sum(case((Item.sold.is_(True), 1), else_=0))
        unsold_count = func.sum(case((Item.sold.is_(False), 1), else_=0))
        total_count = func.count(Item.sku)

        avg_days_listed_unsold = func.avg(
            case(
                (
                    (Item.sold.is_(False)) & (Item.date_listed.isnot(None)),
                    func.julianday(func.current_date()) - func.julianday(Item.date_listed),
                ),
                else_=None,
            )
        )

        rows_counts = (
            db.session.query(
                category_col.label("category"),
                sold_count_all.label("sold_count_all"),
                unsold_count.label("unsold_count"),
                total_count.label("total_count"),
                avg_days_listed_unsold.label("avg_days_listed_unsold"),
            )
            .group_by(category_col)
            .all()
        )

        counts_map = {}
        for r in rows_counts:
            counts_map[r.category] = {
                "category": r.category,
                "unsold_count": int(r.unsold_count or 0),
                "total_count": int(r.total_count or 0),
                "avg_days_listed_unsold": float(r.avg_days_listed_unsold) if r.avg_days_listed_unsold is not None else None,
            }

        # -----------------------------
        # Sold metrics by category (sold in range)
        # -----------------------------
        sold_metrics_q = (
            db.session.query(
                category_col.label("category"),
                func.count(Item.sku).label("sold_count"),
                func.coalesce(func.sum(profit_expr), 0.0).label("total_profit"),
                func.avg(profit_expr).label("avg_profit"),
            )
            .filter(Item.sold.is_(True))
        )
        if sold_date_filters:
            sold_metrics_q = sold_metrics_q.filter(*sold_date_filters)

        sold_rows = sold_metrics_q.group_by(category_col).all()

        sold_map = {}
        for r in sold_rows:
            sold_map[r.category] = {
                "sold_count": int(r.sold_count or 0),
                "total_profit": float(r.total_profit or 0.0),
                "avg_profit": float(r.avg_profit) if r.avg_profit is not None else 0.0,
            }

        # Merge
        by_category = []
        all_cats = sorted(set(list(counts_map.keys()) + list(sold_map.keys())))
        for cat in all_cats:
            c = counts_map.get(cat, {"unsold_count": 0, "total_count": 0, "avg_days_listed_unsold": None})
            s = sold_map.get(cat, {"sold_count": 0, "total_profit": 0.0, "avg_profit": 0.0})

            total_count_val = int(c.get("total_count") or 0)
            sold_count_val = int(s.get("sold_count") or 0)
            unsold_count_val = int(c.get("unsold_count") or 0)
            sold_rate_pct_cat = (sold_count_val * 100.0 / total_count_val) if total_count_val else 0.0

            by_category.append(
                {
                    "category": cat,
                    "sold_count": sold_count_val,
                    "unsold_count": unsold_count_val,
                    "sold_rate_pct": float(sold_rate_pct_cat),
                    "total_profit": float(s.get("total_profit") or 0.0),
                    "avg_profit": float(s.get("avg_profit") or 0.0),
                    "avg_days_listed_unsold": c.get("avg_days_listed_unsold"),
                }
            )

        by_category.sort(key=lambda x: (x["sold_count"], x["total_profit"]), reverse=True)

        # -----------------------------
        # Top profit items (sold in range)
        # -----------------------------
        top_q = (
            db.session.query(
                Item.sku,
                Item.item_name,
                category_col.label("category"),
                profit_expr.label("profit"),
                days_to_sell_expr.label("days_to_sell"),
                Item.date_sold.label("date_sold"),
            )
            .filter(Item.sold.is_(True))
        )
        if sold_date_filters:
            top_q = top_q.filter(*sold_date_filters)

        top_rows = top_q.order_by(profit_expr.desc()).limit(15).all()

        top_profit = []
        for r in top_rows:
            top_profit.append(
                {
                    "sku": r.sku,
                    "item_name": r.item_name,
                    "category": r.category,
                    "profit": float(r.profit or 0.0),
                    "days_to_sell": float(r.days_to_sell) if r.days_to_sell is not None else None,
                    "date_sold": r.date_sold.isoformat() if r.date_sold else None,
                }
            )

        kpis = {
            "total_items": total_items,
            "sold_items": sold_items,
            "sold_rate_pct": sold_rate_pct,
            "total_profit": float(total_profit),
            "avg_profit_per_sold": float(avg_profit_per_sold),
            "avg_days_to_sell": float(avg_days_to_sell),
        }

        return render_template(
            "reports.html",
            kpis=kpis,
            by_category=by_category,
            top_profit=top_profit,
            range_key=range_key,
            start=start_date.isoformat() if start_date else "",
            end=end_date.isoformat() if end_date else "",
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
                source_location=request.form.get("source_location", "").strip() or None,
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
                source_locations = get_distinct_values(Item, Item.source_location)

                return render_template(
                    "item_new.html",
                    categories=categories,
                    sub_categories=sub_categories,
                    platforms=platforms,
                    source_locations=source_locations,  # ✅ NEW
                )

            db.session.add(item)
            db.session.commit()  # assigns SKU

            # Handle uploads
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

                save_path = os.path.join(app.config["UPLOAD_FOLDER"], stored_name)
                f.save(save_path)

                # ✅ shrink + rotate
                process_image(save_path)

                db.session.add(ItemImage(item_sku=item.sku, filename=stored_name))

            db.session.commit()

            flash(f"Created item SKU #{item.sku}.", "success")
            return redirect(url_for("item_detail", sku=item.sku))

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
            item.source_location = request.form.get("source_location", "").strip() or None


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
                source_locations = get_distinct_values(Item, Item.source_location)

                return render_template(
                    "item_edit.html",
                    item=item,
                    categories=categories,
                    sub_categories=sub_categories,
                    platforms=platforms,
                    source_locations=source_locations,  # ✅ NEW
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

                save_path = os.path.join(app.config["UPLOAD_FOLDER"], stored_name)
                f.save(save_path)

                # ✅ shrink + rotate
                process_image(save_path)

                db.session.add(ItemImage(item_sku=item.sku, filename=stored_name))

            db.session.commit()
            flash(f"Updated SKU #{item.sku}.", "success")
            return redirect(url_for("item_detail", sku=item.sku))

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
