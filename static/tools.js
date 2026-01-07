(function () {
  // ---------------------------
  // Profit estimator
  // ---------------------------
  const $ = (id) => document.getElementById(id);

  const estSale = $("estSale");
  const estCog = $("estCog");
  const estShipPreset = $("estShipPreset");
  const estShip = $("estShip");
  const estEbayPct = $("estEbayPct");
  const estEbayFixed = $("estEbayFixed");
  const estAdPct = $("estAdPct");
  const estOut = $("estOut");

  const btnEstimate = $("btnEstimate");
  const btnApply = $("btnApplyEstimate");

  const shipDefaults = {
    small: parseFloat((estShipPreset?.options?.[0]?.textContent || "").match(/\(([\d.]+)\)/)?.[1] || "6.5"),
    med: parseFloat((estShipPreset?.options?.[1]?.textContent || "").match(/\(([\d.]+)\)/)?.[1] || "9.5"),
    large: parseFloat((estShipPreset?.options?.[2]?.textContent || "").match(/\(([\d.]+)\)/)?.[1] || "14.5"),
  };

  function n(v) {
    const x = parseFloat(v);
    return Number.isFinite(x) ? x : 0;
  }

  function computeEstimate() {
    const sale = n(estSale?.value);
    const cog = n(estCog?.value);
    const ship = n(estShip?.value);
    const ebayPct = n(estEbayPct?.value) / 100;
    const ebayFixed = n(estEbayFixed?.value);
    const adPct = n(estAdPct?.value) / 100;

    const ebayFee = sale * ebayPct + ebayFixed;
    const adFee = sale * adPct;

    const profit = sale - (cog + ship + ebayFee + adFee);
    const roi = cog > 0 ? (profit / cog) * 100 : 0;
    const margin = sale > 0 ? (profit / sale) * 100 : 0;

    return { sale, cog, ship, ebayFee, adFee, profit, roi, margin };
  }

  function renderEstimate(o) {
    if (!estOut) return;
    estOut.textContent =
      `Profit: $${o.profit.toFixed(2)} | ROI: ${o.roi.toFixed(1)}% | Margin: ${o.margin.toFixed(1)}% ` +
      `(eBay: $${o.ebayFee.toFixed(2)}, Ads: $${o.adFee.toFixed(2)}, Ship: $${o.ship.toFixed(2)})`;
  }

  if (estShipPreset && estShip) {
    estShipPreset.addEventListener("change", () => {
      const v = estShipPreset.value;
      if (v === "custom") return;
      estShip.value = (shipDefaults[v] ?? 0).toFixed(2);
    });

    // initialize
    if (!estShip.value) {
      estShip.value = (shipDefaults[estShipPreset.value] ?? 0).toFixed(2);
    }
  }

  if (btnEstimate) {
    btnEstimate.addEventListener("click", () => {
      const o = computeEstimate();
      renderEstimate(o);
    });
  }

  if (btnApply) {
    btnApply.addEventListener("click", () => {
      const o = computeEstimate();
      renderEstimate(o);

      // These field IDs must exist in your form:
      // cog, sale_price, shipping, ad_fee, ebay_fee, buyer_paid_amount
      const fCog = document.querySelector('input[name="cog"]');
      const fSale = document.querySelector('input[name="sale_price"]');
      const fShip = document.querySelector('input[name="shipping"]');
      const fAd = document.querySelector('input[name="ad_fee"]');
      const fEbay = document.querySelector('input[name="ebay_fee"]');
      const fBuyerPaid = document.querySelector('input[name="buyer_paid_amount"]');

      if (fCog && estCog) fCog.value = n(estCog.value).toFixed(2);
      if (fSale && estSale) fSale.value = n(estSale.value).toFixed(2);
      if (fShip && estShip) fShip.value = n(estShip.value).toFixed(2);

      // Apply estimated fees into the form so profit on your item list/detail works later
      if (fAd) fAd.value = o.adFee.toFixed(2);
      if (fEbay) fEbay.value = o.ebayFee.toFixed(2);

      // Buyer paid amount: for now, we assume buyer paid == sale price (you can change later)
      if (fBuyerPaid) fBuyerPaid.value = o.sale.toFixed(2);
    });
  }

  // ---------------------------
  // Barcode scanning (native)
  // ---------------------------
  const btnScan = $("btnScanBarcode");
  const btnClose = $("btnCloseBarcode");
  const wrap = $("barcodeScannerWrap");
  const video = $("barcodeVideo");
  const barcodeInput = $("barcode");
  const status = $("barcodeStatus");

  let stream = null;
  let scanning = false;

  function setStatus(msg) {
    if (status) status.textContent = msg || "";
  }

  async function stopCamera() {
    scanning = false;
    if (stream) {
      for (const t of stream.getTracks()) t.stop();
      stream = null;
    }
    if (video) video.srcObject = null;
    if (wrap) wrap.style.display = "none";
  }

  async function startCamera() {
    if (!wrap || !video) return;

    wrap.style.display = "block";
    setStatus("Requesting camera…");

    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
        audio: false,
      });
      video.srcObject = stream;
      await video.play();
      setStatus("Point at the barcode…");

    } catch (e) {
      setStatus("Camera failed. Make sure you are on HTTPS and have camera permission.");
      console.error(e);
      await stopCamera();
      return;
    }
  }

  async function scanLoopNative() {
    if (!("BarcodeDetector" in window)) {
      setStatus("BarcodeDetector not supported on this browser. (We can add a fallback next.)");
      return;
    }

    const formats = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "qr_code"];
    let detector;
    try {
      detector = new BarcodeDetector({ formats });
    } catch (e) {
      detector = new BarcodeDetector();
    }

    scanning = true;

    const tick = async () => {
      if (!scanning || !video) return;

      try {
        const barcodes = await detector.detect(video);
        if (barcodes && barcodes.length) {
          const raw = barcodes[0].rawValue || "";
          if (raw && barcodeInput) {
            barcodeInput.value = raw;
            setStatus(`Scanned: ${raw}`);
            await stopCamera();
            return;
          }
        }
      } catch (e) {
        // some devices throw intermittently; keep trying
      }

      requestAnimationFrame(tick);
    };

    requestAnimationFrame(tick);
  }

  if (btnScan) {
    btnScan.addEventListener("click", async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        setStatus("This browser can’t access camera APIs.");
        return;
      }
      await startCamera();
      await scanLoopNative();
    });
  }

  if (btnClose) {
    btnClose.addEventListener("click", async () => {
      await stopCamera();
      setStatus("");
    });
  }
})();
