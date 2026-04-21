(() => {
  "use strict";

  const STORAGE_KEY = "admind.chats.v2";
  const ACTIVE_KEY = "admind.active.v2";
  const MAX_IMAGE_SIZE = 4 * 1024 * 1024; // 4MB limit for base64

  const $ = (sel) => document.querySelector(sel);
  const els = {
    app: document.querySelector(".app"),
    sidebar: $("#sidebar"),
    chatList: $("#chatList"),
    newChatBtn: $("#newChatBtn"),
    collapseBtn: $("#collapseBtn"),
    openSidebarBtn: $("#openSidebarBtn"),
    clearBtn: $("#clearBtn"),
    modelName: $("#modelName"),
    modelPill: $("#modelPill"),
    visionBadge: $("#visionBadge"),
    chat: $("#chat"),
    messages: $("#messages"),
    welcome: $("#welcome"),
    composer: $("#composer"),
    input: $("#input"),
    sendBtn: $("#sendBtn"),
    stopBtn: $("#stopBtn"),
    attachBtn: $("#attachBtn"),
    fileInput: $("#fileInput"),
    imagePreviewContainer: $("#imagePreviewContainer"),
    imagePreviews: $("#imagePreviews"),
    dragOverlay: $("#dragOverlay"),
  };

  // ---------- State ----------
  let chats = [];
  let activeId = null;
  let abortController = null;
  let isStreaming = false;
  let pendingImages = []; // {dataUrl: string, file?: File}[]

  // ---------- Utils ----------
  const uid = () =>
    Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

  const save = () => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(chats));
      if (activeId) localStorage.setItem(ACTIVE_KEY, activeId);
    } catch (e) {
      console.warn("localStorage save failed", e);
    }
  };

  const load = () => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) chats = JSON.parse(raw) || [];
      activeId = localStorage.getItem(ACTIVE_KEY) || null;
    } catch (e) {
      chats = [];
      activeId = null;
    }
  };

  const getChat = (id) => chats.find((c) => c.id === id) || null;
  const activeChat = () => getChat(activeId);

  const titleFromMessage = (text) => {
    const t = (text || "").trim().replace(/\s+/g, " ");
    if (!t) return "New chat";
    return t.length > 40 ? t.slice(0, 40) + "…" : t;
  };

  const escapeHtml = (s) =>
    s
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");

  const renderMarkdown = (text) => {
    if (typeof window.marked === "undefined") return escapeHtml(text);
    try {
      window.marked.setOptions({
        breaks: true,
        gfm: true,
        headerIds: false,
        mangle: false,
      });
      const raw = window.marked.parse(text || "");
      if (typeof window.DOMPurify !== "undefined") {
        return window.DOMPurify.sanitize(raw);
      }
      return raw;
    } catch (e) {
      return escapeHtml(text);
    }
  };

  const enhanceCodeBlocks = (container) => {
    container.querySelectorAll("pre").forEach((pre) => {
      if (pre.dataset.enhanced) return;
      pre.dataset.enhanced = "1";
      const codeEl = pre.querySelector("code");
      let lang = "";
      if (codeEl) {
        const cls = codeEl.className || "";
        const m = cls.match(/language-([\w-]+)/);
        if (m) lang = m[1];
      }
      const header = document.createElement("div");
      header.className = "code-header";
      const label = document.createElement("span");
      label.textContent = lang || "code";
      const btn = document.createElement("button");
      btn.className = "copy-btn";
      btn.textContent = "Copy";
      btn.type = "button";
      btn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(codeEl ? codeEl.textContent : "");
          btn.textContent = "Copied";
          setTimeout(() => (btn.textContent = "Copy"), 1500);
        } catch {
          btn.textContent = "Failed";
          setTimeout(() => (btn.textContent = "Copy"), 1500);
        }
      });
      header.appendChild(label);
      header.appendChild(btn);
      pre.insertBefore(header, pre.firstChild);
    });
  };

  // ---------- Image handling ----------
  const fileToDataUrl = (file) => {
    return new Promise((resolve, reject) => {
      if (file.size > MAX_IMAGE_SIZE) {
        reject(new Error(`Image too large (max ${MAX_IMAGE_SIZE / 1024 / 1024}MB)`));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  };

  const addPendingImage = async (file) => {
    try {
      const dataUrl = await fileToDataUrl(file);
      pendingImages.push({ dataUrl, file });
      renderPendingImages();
    } catch (e) {
      alert(e.message || "Failed to load image");
    }
  };

  const removePendingImage = (index) => {
    pendingImages.splice(index, 1);
    renderPendingImages();
  };

  const clearPendingImages = () => {
    pendingImages = [];
    renderPendingImages();
  };

  const renderPendingImages = () => {
    els.imagePreviews.innerHTML = "";
    if (pendingImages.length === 0) {
      els.imagePreviewContainer.classList.add("hidden");
      return;
    }
    els.imagePreviewContainer.classList.remove("hidden");
    pendingImages.forEach((img, i) => {
      const preview = document.createElement("div");
      preview.className = "image-preview";
      
      const imgEl = document.createElement("img");
      imgEl.src = img.dataUrl;
      imgEl.alt = "Preview";
      
      const removeBtn = document.createElement("button");
      removeBtn.className = "remove-btn";
      removeBtn.type = "button";
      removeBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>';
      removeBtn.addEventListener("click", () => removePendingImage(i));
      
      preview.appendChild(imgEl);
      preview.appendChild(removeBtn);
      els.imagePreviews.appendChild(preview);
    });
  };

  // Extract text content from multimodal message
  const getTextFromContent = (content) => {
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
      return content
        .filter((c) => c.type === "text")
        .map((c) => c.text)
        .join("\n");
    }
    return "";
  };

  // Extract images from multimodal message
  const getImagesFromContent = (content) => {
    if (!Array.isArray(content)) return [];
    return content
      .filter((c) => c.type === "image_url")
      .map((c) => c.image_url?.url || "");
  };

  // ---------- Chat CRUD ----------
  const createChat = () => {
    const c = {
      id: uid(),
      title: "New chat",
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    chats.unshift(c);
    activeId = c.id;
    save();
    renderSidebar();
    renderMessages();
    return c;
  };

  const deleteChat = (id) => {
    chats = chats.filter((c) => c.id !== id);
    if (activeId === id) activeId = chats[0]?.id || null;
    if (!activeId) createChat();
    save();
    renderSidebar();
    renderMessages();
  };

  const selectChat = (id) => {
    if (activeId === id) return;
    activeId = id;
    clearPendingImages();
    save();
    renderSidebar();
    renderMessages();
  };

  const clearActive = () => {
    const c = activeChat();
    if (!c) return;
    c.messages = [];
    c.title = "New chat";
    c.updatedAt = Date.now();
    clearPendingImages();
    save();
    renderSidebar();
    renderMessages();
  };

  // ---------- Rendering ----------
  const renderSidebar = () => {
    els.chatList.innerHTML = "";
    chats.forEach((c) => {
      const item = document.createElement("div");
      item.className = "chat-item" + (c.id === activeId ? " active" : "");
      item.dataset.id = c.id;

      const title = document.createElement("div");
      title.className = "title";
      title.textContent = c.title || "New chat";

      const del = document.createElement("button");
      del.className = "del";
      del.type = "button";
      del.title = "Delete chat";
      del.innerHTML =
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14z"/></svg>';
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        if (confirm("Delete this chat?")) deleteChat(c.id);
      });

      item.appendChild(title);
      item.appendChild(del);
      item.addEventListener("click", () => selectChat(c.id));
      els.chatList.appendChild(item);
    });
  };

  const renderMessages = () => {
    const c = activeChat();
    els.messages.innerHTML = "";
    if (!c || c.messages.length === 0) {
      els.welcome.classList.remove("hidden");
      return;
    }
    els.welcome.classList.add("hidden");
    c.messages.forEach((m) => {
      const text = getTextFromContent(m.content);
      const images = getImagesFromContent(m.content);
      appendMessageDom(m.role, text, images);
    });
    scrollToBottom(true);
  };

  const appendMessageDom = (role, content, images = []) => {
    const wrap = document.createElement("div");
    wrap.className = "msg " + role;

    const inner = document.createElement("div");
    inner.className = "inner";

    if (role === "assistant") {
      const av = document.createElement("div");
      av.className = "avatar bot";
      av.textContent = "⚡";
      inner.appendChild(av);
      const contentEl = document.createElement("div");
      contentEl.className = "content";
      contentEl.innerHTML = renderMarkdown(content);
      enhanceCodeBlocks(contentEl);
      inner.appendChild(contentEl);
    } else {
      // User message
      const msgContent = document.createElement("div");
      msgContent.className = "user-msg-content";
      
      // Show images if any
      if (images.length > 0) {
        const imagesDiv = document.createElement("div");
        imagesDiv.className = "msg-images";
        images.forEach((src) => {
          const img = document.createElement("img");
          img.src = src;
          img.alt = "Uploaded image";
          img.addEventListener("click", () => openLightbox(src));
          imagesDiv.appendChild(img);
        });
        msgContent.appendChild(imagesDiv);
      }
      
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.textContent = content;
      msgContent.appendChild(bubble);
      
      inner.appendChild(msgContent);
      
      const av = document.createElement("div");
      av.className = "avatar user";
      av.textContent = "You";
      av.style.fontSize = "11px";
      inner.appendChild(av);
    }

    wrap.appendChild(inner);
    els.messages.appendChild(wrap);
    return wrap;
  };

  const scrollToBottom = (force = false) => {
    const el = els.chat;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (force || nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  };

  // ---------- Lightbox ----------
  let lightboxEl = null;
  
  const openLightbox = (src) => {
    if (!lightboxEl) {
      lightboxEl = document.createElement("div");
      lightboxEl.className = "lightbox hidden";
      lightboxEl.innerHTML = `
        <button class="lightbox-close">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M18 6L6 18M6 6l12 12"/>
          </svg>
        </button>
        <img src="" alt="Full size" />
      `;
      lightboxEl.addEventListener("click", closeLightbox);
      document.body.appendChild(lightboxEl);
    }
    lightboxEl.querySelector("img").src = src;
    lightboxEl.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  };
  
  const closeLightbox = () => {
    if (lightboxEl) {
      lightboxEl.classList.add("hidden");
      document.body.style.overflow = "";
    }
  };

  // ---------- Streaming chat ----------
  const setStreaming = (on) => {
    isStreaming = on;
    els.sendBtn.disabled = on;
    els.stopBtn.classList.toggle("hidden", !on);
    if (on) {
      els.input.setAttribute("disabled", "true");
      els.attachBtn.setAttribute("disabled", "true");
    } else {
      els.input.removeAttribute("disabled");
      els.attachBtn.removeAttribute("disabled");
      els.input.focus();
    }
  };

  const sendMessage = async (text) => {
    const content = (text || "").trim();
    if ((!content && pendingImages.length === 0) || isStreaming) return;

    let c = activeChat();
    if (!c) c = createChat();

    // Build multimodal content if images present
    let messageContent;
    let displayImages = [];
    
    if (pendingImages.length > 0) {
      messageContent = [];
      pendingImages.forEach((img) => {
        messageContent.push({
          type: "image_url",
          image_url: { url: img.dataUrl }
        });
        displayImages.push(img.dataUrl);
      });
      if (content) {
        messageContent.push({ type: "text", text: content });
      } else {
        messageContent.push({ type: "text", text: "What's in this image?" });
      }
    } else {
      messageContent = content;
    }

    c.messages.push({ role: "user", content: messageContent });
    if (!c.title || c.title === "New chat") {
      c.title = titleFromMessage(content || "Image analysis");
    }
    c.updatedAt = Date.now();
    save();
    renderSidebar();

    els.welcome.classList.add("hidden");
    appendMessageDom("user", content || "What's in this image?", displayImages);
    clearPendingImages();
    scrollToBottom(true);

    const assistantWrap = appendMessageDom("assistant", "");
    const contentEl = assistantWrap.querySelector(".content");
    contentEl.classList.add("typing");
    scrollToBottom(true);

    setStreaming(true);
    abortController = new AbortController();
    let accumulated = "";

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: c.messages.map((m) => ({
            role: m.role,
            content: m.content,
          })),
          stream: true,
        }),
        signal: abortController.signal,
      });

      if (!res.ok || !res.body) {
        const msg = "Request failed: " + res.status + " " + res.statusText;
        contentEl.classList.remove("typing");
        contentEl.innerHTML = renderMarkdown("**Error.** " + msg);
        throw new Error(msg);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const event = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const line = event.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;
          try {
            const obj = JSON.parse(payload);
            if (obj.delta) {
              accumulated += obj.delta;
              contentEl.innerHTML = renderMarkdown(accumulated);
              scrollToBottom();
            } else if (obj.error) {
              accumulated += "\n\n**Error:** " + obj.error;
              contentEl.innerHTML = renderMarkdown(accumulated);
            } else if (obj.done) {
              // end of stream
            }
          } catch {
            // ignore malformed chunk
          }
        }
      }
    } catch (e) {
      if (e.name === "AbortError") {
        accumulated += accumulated ? "\n\n_(stopped)_" : "_(stopped)_";
        contentEl.innerHTML = renderMarkdown(accumulated);
      } else {
        console.error(e);
      }
    } finally {
      contentEl.classList.remove("typing");
      enhanceCodeBlocks(contentEl);
      abortController = null;
      setStreaming(false);
      if (accumulated.trim()) {
        c.messages.push({ role: "assistant", content: accumulated });
        c.updatedAt = Date.now();
        save();
      } else {
        assistantWrap.remove();
      }
      scrollToBottom(true);
    }
  };

  const stopGeneration = () => {
    if (abortController) abortController.abort();
  };

  // ---------- Composer ----------
  const autoResize = () => {
    const ta = els.input;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
  };

  els.input.addEventListener("input", autoResize);
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (isStreaming) return;
      const text = els.input.value;
      els.input.value = "";
      autoResize();
      sendMessage(text);
    }
  });

  els.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    if (isStreaming) return;
    const text = els.input.value;
    els.input.value = "";
    autoResize();
    sendMessage(text);
  });

  els.stopBtn.addEventListener("click", stopGeneration);
  els.newChatBtn.addEventListener("click", () => {
    if (isStreaming) stopGeneration();
    clearPendingImages();
    createChat();
    els.input.focus();
  });
  els.clearBtn.addEventListener("click", () => {
    if (confirm("Clear this conversation?")) {
      if (isStreaming) stopGeneration();
      clearActive();
    }
  });

  // ---------- Image upload handlers ----------
  els.attachBtn.addEventListener("click", () => {
    els.fileInput.click();
  });

  els.fileInput.addEventListener("change", async (e) => {
    const files = e.target.files;
    if (!files) return;
    for (const file of files) {
      if (file.type.startsWith("image/")) {
        await addPendingImage(file);
      }
    }
    els.fileInput.value = "";
  });

  // Paste handler
  document.addEventListener("paste", async (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) await addPendingImage(file);
      }
    }
  });

  // Drag and drop
  let dragCounter = 0;
  
  document.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragCounter++;
    if (e.dataTransfer?.types.includes("Files")) {
      els.dragOverlay.classList.remove("hidden");
    }
  });

  document.addEventListener("dragleave", (e) => {
    e.preventDefault();
    dragCounter--;
    if (dragCounter === 0) {
      els.dragOverlay.classList.add("hidden");
    }
  });

  document.addEventListener("dragover", (e) => {
    e.preventDefault();
  });

  document.addEventListener("drop", async (e) => {
    e.preventDefault();
    dragCounter = 0;
    els.dragOverlay.classList.add("hidden");
    
    const files = e.dataTransfer?.files;
    if (!files) return;
    for (const file of files) {
      if (file.type.startsWith("image/")) {
        await addPendingImage(file);
      }
    }
  });

  // Sidebar toggles
  const isMobile = () => window.innerWidth <= 767;
  
  const closeSidebar = () => {
    if (isMobile()) {
      els.app.classList.remove("sidebar-open");
    } else {
      els.app.classList.add("sidebar-collapsed");
    }
  };
  
  const openSidebar = () => {
    if (isMobile()) {
      els.app.classList.add("sidebar-open");
    } else {
      els.app.classList.remove("sidebar-collapsed");
    }
  };
  
  els.collapseBtn.addEventListener("click", closeSidebar);
  els.openSidebarBtn.addEventListener("click", openSidebar);
  
  // Close sidebar when clicking outside (mobile overlay)
  document.addEventListener("click", (e) => {
    if (!isMobile() || !els.app.classList.contains("sidebar-open")) return;
    const clickedInSidebar = els.sidebar.contains(e.target);
    const clickedMenuBtn = els.openSidebarBtn.contains(e.target);
    if (!clickedInSidebar && !clickedMenuBtn) {
      closeSidebar();
    }
  });
  
  // Close sidebar after selecting a chat on mobile
  els.chatList.addEventListener("click", (e) => {
    if (isMobile() && e.target.closest(".chat-item")) {
      setTimeout(closeSidebar, 150);
    }
  });

  // Suggestion buttons
  document.querySelectorAll(".suggestion").forEach((btn) => {
    btn.addEventListener("click", () => {
      const action = btn.dataset.action;
      if (action === "upload") {
        els.fileInput.click();
        return;
      }
      const p = btn.dataset.prompt || btn.textContent.trim();
      els.input.value = p;
      autoResize();
      els.input.focus();
    });
  });

  // Escape to close lightbox
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeLightbox();
    }
  });

  // ---------- Health / model ----------
  const refreshHealth = async () => {
    try {
      const res = await fetch("/api/health");
      const data = await res.json();
      els.modelName.textContent = data.model || "local-model";
      els.modelPill.classList.remove("offline");
      els.modelPill.title = `${data.model}\n${data.endpoint || ""}`;
      
      // Show vision badge if supported
      if (data.vision) {
        els.visionBadge?.classList.remove("hidden");
      } else {
        els.visionBadge?.classList.add("hidden");
      }
    } catch {
      els.modelName.textContent = "offline";
      els.modelPill.classList.add("offline");
      els.visionBadge?.classList.add("hidden");
    }
  };

  // ---------- Init ----------
  const init = () => {
    load();
    if (chats.length === 0) createChat();
    if (!getChat(activeId)) activeId = chats[0].id;
    renderSidebar();
    renderMessages();
    autoResize();
    refreshHealth();
    setInterval(refreshHealth, 20000);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
