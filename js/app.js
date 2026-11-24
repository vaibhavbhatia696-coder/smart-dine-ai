
let selectedCategory = "All";
let cart = {};

function updateCartCount(count) {
    const element = document.getElementById("cart-count");
    if (element) element.textContent = count;
}

function showCartMessage(message) {
    console.error(message);
    alert(message);
}

async function updateCart(id, change) {
    const currentQuantity = Number(cart[id] || 0);
    const newQuantity = currentQuantity + change;

    try {
        const response = await fetch("/api/cart", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                id: id,
                quantity: newQuantity
            })
        });

        const data = await response.json();

        if (!response.ok) {
            showCartMessage(data.error || "Unable to update cart.");
            return;
        }

        renderCartData(data);
    } catch (error) {
        showCartMessage("Connection error. Please try again.");
    }
}

function addToCart(id) {
    updateCart(id, 1);
    openCart();
}

function renderCartData(data) {
    cart = data.cart || {};
    updateCartCount(data.count || 0);

    const box = document.getElementById("drawer-items");
    const totalElement = document.getElementById("cart-total");

    if (!box || !totalElement) return;

    box.innerHTML = "";

    if (!data.items || data.items.length === 0) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = "Your cart is waiting for something delicious.";
        box.appendChild(empty);
    } else {
        data.items.forEach(item => {
            const line = document.createElement("div");
            line.className = "cart-line";

            const details = document.createElement("div");

            const name = document.createElement("b");
            name.textContent = item.name;

            const price = document.createElement("small");
            price.textContent = `₹${item.price} each`;

            details.appendChild(name);
            details.appendChild(price);

            const quantityBox = document.createElement("div");
            quantityBox.className = "qty";

            const minus = document.createElement("button");
            minus.type = "button";
            minus.textContent = "−";
            minus.addEventListener("click", () => {
                updateCart(item.id, -1);
            });

            const quantity = document.createElement("b");
            quantity.textContent = item.quantity;

            const plus = document.createElement("button");
            plus.type = "button";
            plus.textContent = "+";
            plus.addEventListener("click", () => {
                updateCart(item.id, 1);
            });

            quantityBox.appendChild(minus);
            quantityBox.appendChild(quantity);
            quantityBox.appendChild(plus);

            line.appendChild(details);
            line.appendChild(quantityBox);

            box.appendChild(line);
        });
    }

    totalElement.textContent = "₹" + Number(data.total || 0).toFixed(2);
}

async function loadCart() {
    try {
        const response = await fetch("/api/cart");
        const data = await response.json();

        if (response.ok) {
            renderCartData(data);
        }
    } catch (error) {
        console.error("Could not load cart:", error);
    }
}

function openCart() {
    document.getElementById("cart-drawer")?.classList.add("open");
    document.getElementById("drawer-overlay")?.classList.add("show");
}

function closeCart() {
    document.getElementById("cart-drawer")?.classList.remove("open");
    document.getElementById("drawer-overlay")?.classList.remove("show");
}

function toggleCart() {
    const drawer = document.getElementById("cart-drawer");
    const overlay = document.getElementById("drawer-overlay");

    if (!drawer) return;

    drawer.classList.toggle("open");
    overlay?.classList.toggle("show");
}

function setCategory(category, button) {
    selectedCategory = category;

    document.querySelectorAll(".category").forEach(element => {
        element.classList.remove("active");
    });

    button?.classList.add("active");
    filterMenu();
}

function filterMenu() {
    const search = (
        document.getElementById("search")?.value || ""
    ).toLowerCase();

    let shown = 0;

    document.querySelectorAll(".food-card").forEach(card => {
        const category = card.dataset.category || "";
        const name = card.dataset.name || "";

        const categoryMatch =
            selectedCategory === "All" || category === selectedCategory;

        const searchMatch = name.includes(search);

        const visible = categoryMatch && searchMatch;

        card.style.display = visible ? "" : "none";

        if (visible) shown++;
    });

    const emptyState = document.getElementById("empty-state");

    if (emptyState) {
        emptyState.hidden = shown !== 0;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById("cart-drawer")) {
        loadCart();
    }
});
