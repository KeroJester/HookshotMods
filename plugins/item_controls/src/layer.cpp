// Vulkan layer VK_LAYER_HOOKSHOT_item_controls: how the Item Controls code gets into the game on Linux and Windows.
// Hookshot loads only RomFS mods, but it reads hookshot_env.txt before it starts Vulkan, and the Vulkan loader
// then loads this layer from the mod folder (VK_ADD_LAYER_PATH + VK_INSTANCE_LAYERS, written by the mod manager).
// The layer:
//   - starts the plugin (game hooks) when the game creates its Vulkan instance
//   - draws the six item boxes over every presented frame (ImGui, dynamic rendering), waiting on the game's
//     render-finished semaphores and making the present wait on ours
// Everything else is passed straight down the chain.
#include "platform.h"
#include "item_controls.h"
#include "imgui.h"
#include "imgui_impl_vulkan.h"
#include <vulkan/vk_layer.h>
#include <algorithm>
#include <cstring>
#include <mutex>
#include <unordered_map>
#include <vector>

#ifdef _WIN32
#define LAYER_EXPORT extern "C" __declspec(dllexport)
#else
#define LAYER_EXPORT extern "C" __attribute__((visibility("default")))
#endif

using plat::Log;

namespace {

void* Key(const void* dispatchable) { return *(void**)dispatchable; }

struct Inst {
    VkInstance inst = VK_NULL_HANDLE;
    PFN_vkGetInstanceProcAddr gipa = nullptr;
    PFN_vkDestroyInstance DestroyInstance = nullptr;
};

struct Swap {
    VkFormat format = VK_FORMAT_UNDEFINED;
    VkExtent2D extent{};
    std::vector<VkImage> images;
    std::vector<VkImageView> views;
    std::vector<VkCommandBuffer> cmds;
    std::vector<VkFence> fences;            // signalled when that image's overlay commands finished
    std::vector<VkSemaphore> done;          // the present waits on this instead of the game's semaphores
};

struct Dev {
    VkDevice dev = VK_NULL_HANDLE;
    VkPhysicalDevice phys = VK_NULL_HANDLE;
    VkInstance inst = VK_NULL_HANDLE;
    PFN_vkGetDeviceProcAddr gdpa = nullptr;
    std::unordered_map<VkQueue, uint32_t> queueFamily;
    std::unordered_map<VkSwapchainKHR, Swap> swaps;
    VkCommandPool pool = VK_NULL_HANDLE;
    uint32_t poolFamily = ~0u;
#define DF(name) PFN_vk##name name = nullptr;
    DF(DestroyDevice) DF(GetDeviceQueue) DF(GetDeviceQueue2) DF(CreateSwapchainKHR) DF(DestroySwapchainKHR)
    DF(GetSwapchainImagesKHR) DF(QueuePresentKHR) DF(CreateImageView) DF(DestroyImageView) DF(CreateCommandPool)
    DF(DestroyCommandPool) DF(AllocateCommandBuffers) DF(FreeCommandBuffers) DF(CreateFence) DF(DestroyFence)
    DF(WaitForFences) DF(ResetFences) DF(CreateSemaphore) DF(DestroySemaphore) DF(ResetCommandBuffer)
    DF(BeginCommandBuffer) DF(EndCommandBuffer) DF(CmdPipelineBarrier) DF(QueueSubmit) DF(CmdBeginRendering)
    DF(CmdEndRendering) DF(DeviceWaitIdle)
#undef DF
};

std::mutex g_Mu;
std::unordered_map<void*, Inst> g_Inst;
std::unordered_map<void*, Dev> g_Dev;
bool g_PluginStarted = false, g_PluginOk = false;

// ImGui (one context, for the device that presents)
VkDevice g_ImGuiDev = VK_NULL_HANDLE;
VkFormat g_ImGuiFormat = VK_FORMAT_UNDEFINED;
bool g_ImGuiOk = false;
int64_t g_LastFrame = 0;

void StartPlugin() {
    if (g_PluginStarted) return;
    g_PluginStarted = true;
    // the library sits in <mod>/layer/<platform>/: the mod folder is two levels up
    std::string dir = plat::LibraryDir();
    std::string mod = dir.substr(0, dir.find_last_of("/\\"));
    mod = mod.substr(0, mod.find_last_of("/\\"));
    std::string name = mod.substr(mod.find_last_of("/\\") + 1);
    plat::LogOpen(mod + plat::PathSep() + "item_controls" + plat::PathSep() + "itemctl.log");
    Log("[layer] VK_LAYER_HOOKSHOT_item_controls loaded from %s\n", dir.c_str());
    if (!name.empty() && (name[0] == '_' || name[0] == '-')) { Log("[layer] mod folder %s is disabled: off\n", name.c_str()); return; }
    g_PluginOk = itemctl::Init(mod);
}

VkLayerInstanceCreateInfo* InstChain(const VkInstanceCreateInfo* ci) {
    auto* p = (VkLayerInstanceCreateInfo*)ci->pNext;
    while (p && !(p->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO && p->function == VK_LAYER_LINK_INFO))
        p = (VkLayerInstanceCreateInfo*)p->pNext;
    return p;
}
VkLayerDeviceCreateInfo* DevChain(const VkDeviceCreateInfo* ci) {
    auto* p = (VkLayerDeviceCreateInfo*)ci->pNext;
    while (p && !(p->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO && p->function == VK_LAYER_LINK_INFO))
        p = (VkLayerDeviceCreateInfo*)p->pNext;
    return p;
}

// ---- instance --------------------------------------------------------------------------------------------------
VKAPI_ATTR VkResult VKAPI_CALL CreateInstance(const VkInstanceCreateInfo* ci, const VkAllocationCallbacks* alloc, VkInstance* out) {
    VkLayerInstanceCreateInfo* chain = InstChain(ci);
    if (!chain) return VK_ERROR_INITIALIZATION_FAILED;
    PFN_vkGetInstanceProcAddr next = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    chain->u.pLayerInfo = chain->u.pLayerInfo->pNext;
    auto create = (PFN_vkCreateInstance)next(VK_NULL_HANDLE, "vkCreateInstance");
    VkResult r = create(ci, alloc, out);
    if (r != VK_SUCCESS) return r;
    std::lock_guard<std::mutex> lk(g_Mu);
    Inst& in = g_Inst[Key(*out)];
    in.inst = *out;
    in.gipa = next;
    in.DestroyInstance = (PFN_vkDestroyInstance)next(*out, "vkDestroyInstance");
    StartPlugin();
    return r;
}

VKAPI_ATTR void VKAPI_CALL DestroyInstance(VkInstance inst, const VkAllocationCallbacks* alloc) {
    PFN_vkDestroyInstance fn;
    {
        std::lock_guard<std::mutex> lk(g_Mu);
        auto it = g_Inst.find(Key(inst));
        if (it == g_Inst.end()) return;
        fn = it->second.DestroyInstance;
        g_Inst.erase(it);
    }
    fn(inst, alloc);
}

// ---- device -----------------------------------------------------------------------------------------------------
VKAPI_ATTR VkResult VKAPI_CALL CreateDevice(VkPhysicalDevice phys, const VkDeviceCreateInfo* ci, const VkAllocationCallbacks* alloc, VkDevice* out) {
    VkLayerDeviceCreateInfo* chain = DevChain(ci);
    if (!chain) return VK_ERROR_INITIALIZATION_FAILED;
    PFN_vkGetInstanceProcAddr gipa = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    PFN_vkGetDeviceProcAddr gdpa = chain->u.pLayerInfo->pfnNextGetDeviceProcAddr;
    chain->u.pLayerInfo = chain->u.pLayerInfo->pNext;
    auto create = (PFN_vkCreateDevice)gipa(VK_NULL_HANDLE, "vkCreateDevice");
    // the swapchain images get COLOR_ATTACHMENT usage (below); everything else is the game's own request
    VkResult r = create(phys, ci, alloc, out);
    if (r != VK_SUCCESS) return r;
    std::lock_guard<std::mutex> lk(g_Mu);
    Dev& d = g_Dev[Key(*out)];
    d.dev = *out;
    d.phys = phys;
    d.gdpa = gdpa;
    for (auto& kv : g_Inst) d.inst = kv.second.inst;      // (the game has one instance)
#define DF(name) d.name = (PFN_vk##name)gdpa(*out, "vk" #name);
    DF(DestroyDevice) DF(GetDeviceQueue) DF(GetDeviceQueue2) DF(CreateSwapchainKHR) DF(DestroySwapchainKHR)
    DF(GetSwapchainImagesKHR) DF(QueuePresentKHR) DF(CreateImageView) DF(DestroyImageView) DF(CreateCommandPool)
    DF(DestroyCommandPool) DF(AllocateCommandBuffers) DF(FreeCommandBuffers) DF(CreateFence) DF(DestroyFence)
    DF(WaitForFences) DF(ResetFences) DF(CreateSemaphore) DF(DestroySemaphore) DF(ResetCommandBuffer)
    DF(BeginCommandBuffer) DF(EndCommandBuffer) DF(CmdPipelineBarrier) DF(QueueSubmit) DF(CmdBeginRendering)
    DF(CmdEndRendering) DF(DeviceWaitIdle)
#undef DF
    if (!d.CmdBeginRendering) d.CmdBeginRendering = (PFN_vkCmdBeginRendering)gdpa(*out, "vkCmdBeginRenderingKHR");
    if (!d.CmdEndRendering) d.CmdEndRendering = (PFN_vkCmdEndRendering)gdpa(*out, "vkCmdEndRenderingKHR");
    return r;
}

void FreeSwap(Dev& d, Swap& s) {
    if (!s.fences.empty()) d.WaitForFences(d.dev, (uint32_t)s.fences.size(), s.fences.data(), VK_TRUE, 1000000000ULL);
    for (auto v : s.views) if (v) d.DestroyImageView(d.dev, v, nullptr);
    for (auto f : s.fences) if (f) d.DestroyFence(d.dev, f, nullptr);
    for (auto m : s.done) if (m) d.DestroySemaphore(d.dev, m, nullptr);
    if (!s.cmds.empty() && d.pool) d.FreeCommandBuffers(d.dev, d.pool, (uint32_t)s.cmds.size(), s.cmds.data());
    s = Swap{};
}

VKAPI_ATTR void VKAPI_CALL DestroyDevice(VkDevice dev, const VkAllocationCallbacks* alloc) {
    PFN_vkDestroyDevice fn;
    {
        std::lock_guard<std::mutex> lk(g_Mu);
        auto it = g_Dev.find(Key(dev));
        if (it == g_Dev.end()) return;
        Dev& d = it->second;
        d.DeviceWaitIdle(dev);
        if (g_ImGuiDev == dev && g_ImGuiOk) {
            ImGui_ImplVulkan_Shutdown();
            ImGui::DestroyContext();
            g_ImGuiOk = false;
            g_ImGuiDev = VK_NULL_HANDLE;
        }
        for (auto& kv : d.swaps) FreeSwap(d, kv.second);
        if (d.pool) d.DestroyCommandPool(dev, d.pool, nullptr);
        fn = d.DestroyDevice;
        g_Dev.erase(it);
    }
    fn(dev, alloc);
}

VKAPI_ATTR void VKAPI_CALL GetDeviceQueue(VkDevice dev, uint32_t family, uint32_t index, VkQueue* q) {
    std::lock_guard<std::mutex> lk(g_Mu);
    Dev& d = g_Dev[Key(dev)];
    d.GetDeviceQueue(dev, family, index, q);
    if (*q) d.queueFamily[*q] = family;
}
VKAPI_ATTR void VKAPI_CALL GetDeviceQueue2(VkDevice dev, const VkDeviceQueueInfo2* info, VkQueue* q) {
    std::lock_guard<std::mutex> lk(g_Mu);
    Dev& d = g_Dev[Key(dev)];
    d.GetDeviceQueue2(dev, info, q);
    if (*q) d.queueFamily[*q] = info->queueFamilyIndex;
}

// ---- swapchain ---------------------------------------------------------------------------------------------------
VKAPI_ATTR VkResult VKAPI_CALL CreateSwapchainKHR(VkDevice dev, const VkSwapchainCreateInfoKHR* ci, const VkAllocationCallbacks* alloc, VkSwapchainKHR* out) {
    std::lock_guard<std::mutex> lk(g_Mu);
    Dev& d = g_Dev[Key(dev)];
    VkSwapchainCreateInfoKHR mod = *ci;
    mod.imageUsage |= VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;   // so the overlay can render into the images
    VkResult r = d.CreateSwapchainKHR(dev, &mod, alloc, out);
    bool ours = r == VK_SUCCESS;
    if (!ours) r = d.CreateSwapchainKHR(dev, ci, alloc, out);
    if (r != VK_SUCCESS) return r;
    Swap& s = d.swaps[*out];
    s.format = ci->imageFormat;
    s.extent = ci->imageExtent;
    if (!ours || !g_PluginOk) return r;                   // no overlay on this swapchain
    uint32_t n = 0;
    d.GetSwapchainImagesKHR(dev, *out, &n, nullptr);
    s.images.resize(n);
    d.GetSwapchainImagesKHR(dev, *out, &n, s.images.data());
    s.views.assign(n, VK_NULL_HANDLE);
    s.fences.assign(n, VK_NULL_HANDLE);
    s.done.assign(n, VK_NULL_HANDLE);
    for (uint32_t i = 0; i < n; i++) {
        VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
        vi.image = s.images[i];
        vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
        vi.format = s.format;
        vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
        d.CreateImageView(dev, &vi, nullptr, &s.views[i]);
        VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
        fi.flags = VK_FENCE_CREATE_SIGNALED_BIT;
        d.CreateFence(dev, &fi, nullptr, &s.fences[i]);
        VkSemaphoreCreateInfo si{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
        d.CreateSemaphore(dev, &si, nullptr, &s.done[i]);
    }
    Log("[layer] swapchain %ux%u format %d, %u images\n", s.extent.width, s.extent.height, (int)s.format, n);
    return r;
}

VKAPI_ATTR void VKAPI_CALL DestroySwapchainKHR(VkDevice dev, VkSwapchainKHR sc, const VkAllocationCallbacks* alloc) {
    std::lock_guard<std::mutex> lk(g_Mu);
    Dev& d = g_Dev[Key(dev)];
    auto it = d.swaps.find(sc);
    if (it != d.swaps.end()) { FreeSwap(d, it->second); d.swaps.erase(it); }
    d.DestroySwapchainKHR(dev, sc, alloc);
}

PFN_vkVoidFunction ImGuiLoader(const char* name, void* user) {
    Dev* d = (Dev*)user;
    PFN_vkVoidFunction f = d->gdpa(d->dev, name);
    if (!f) {
        for (auto& kv : g_Inst) if (kv.second.inst == d->inst) f = kv.second.gipa(d->inst, name);
    }
    return f;
}

bool EnsureImGui(Dev& d, VkQueue q, uint32_t family, const Swap& s) {
    if (g_ImGuiOk && g_ImGuiDev == d.dev && g_ImGuiFormat == s.format) return true;
    if (g_ImGuiOk) { ImGui_ImplVulkan_Shutdown(); ImGui::DestroyContext(); g_ImGuiOk = false; }
    if (!d.CmdBeginRendering || !d.CmdEndRendering) return false;
    if (!ImGui_ImplVulkan_LoadFunctions(VK_API_VERSION_1_3, ImGuiLoader, &d)) { Log("[layer] ImGui: Vulkan functions missing\n"); return false; }
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.LogFilename = nullptr;
    ImGui_ImplVulkan_InitInfo ii{};
    ii.ApiVersion = VK_API_VERSION_1_3;
    ii.Instance = d.inst;
    ii.PhysicalDevice = d.phys;
    ii.Device = d.dev;
    ii.QueueFamily = family;
    ii.Queue = q;
    ii.DescriptorPoolSize = 64;
    ii.MinImageCount = 2;
    ii.ImageCount = (uint32_t)std::max<size_t>(2, s.images.size());
    ii.UseDynamicRendering = true;
    VkPipelineRenderingCreateInfoKHR ri{VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO_KHR};
    ri.colorAttachmentCount = 1;
    static VkFormat fmt;
    fmt = s.format;
    ri.pColorAttachmentFormats = &fmt;
    ii.PipelineInfoMain.PipelineRenderingCreateInfo = ri;
    if (!ImGui_ImplVulkan_Init(&ii)) { Log("[layer] ImGui_ImplVulkan_Init failed\n"); ImGui::DestroyContext(); return false; }
    g_ImGuiOk = true;
    g_ImGuiDev = d.dev;
    g_ImGuiFormat = s.format;
    Log("[layer] overlay renderer ready (family %u)\n", family);
    return true;
}

// Draws the boxes into image `idx`; returns the semaphore the present must wait on (or null: nothing drawn)
VkSemaphore DrawOverlay(Dev& d, VkQueue q, Swap& s, uint32_t idx, const VkPresentInfoKHR* pi) {
    if (!g_PluginOk || idx >= s.views.size() || !s.views[idx] || !itemctl::HudVisible()) return VK_NULL_HANDLE;
    auto fam = d.queueFamily.find(q);
    if (fam == d.queueFamily.end()) return VK_NULL_HANDLE;
    if (!EnsureImGui(d, q, fam->second, s)) return VK_NULL_HANDLE;
    if (!d.pool || d.poolFamily != fam->second) {
        if (d.pool) { d.DeviceWaitIdle(d.dev); d.DestroyCommandPool(d.dev, d.pool, nullptr); d.pool = VK_NULL_HANDLE; }
        VkCommandPoolCreateInfo ci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
        ci.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
        ci.queueFamilyIndex = fam->second;
        if (d.CreateCommandPool(d.dev, &ci, nullptr, &d.pool) != VK_SUCCESS) return VK_NULL_HANDLE;
        d.poolFamily = fam->second;
        for (auto& kv : d.swaps) kv.second.cmds.clear();
    }
    if (s.cmds.size() != s.images.size()) {
        s.cmds.assign(s.images.size(), VK_NULL_HANDLE);
        VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
        ai.commandPool = d.pool;
        ai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
        ai.commandBufferCount = (uint32_t)s.cmds.size();
        if (d.AllocateCommandBuffers(d.dev, &ai, s.cmds.data()) != VK_SUCCESS) { s.cmds.clear(); return VK_NULL_HANDLE; }
    }
    // ImGui frame
    ImGuiIO& io = ImGui::GetIO();
    io.DisplaySize = ImVec2((float)s.extent.width, (float)s.extent.height);
    int64_t now = plat::NowNs();
    io.DeltaTime = g_LastFrame ? std::max(1e-4f, (float)((now - g_LastFrame) * 1e-9)) : 1.0f / 60;
    g_LastFrame = now;
    ImGui_ImplVulkan_NewFrame();
    ImGui::NewFrame();
    itemctl::DrawHud();
    ImGui::Render();
    ImDrawData* dd = ImGui::GetDrawData();
    if (dd && dd->Textures)
        for (ImTextureData* t : *dd->Textures) if (t->Status != ImTextureStatus_OK) ImGui_ImplVulkan_UpdateTexture(t);
    if (!dd || dd->CmdListsCount == 0) return VK_NULL_HANDLE;

    d.WaitForFences(d.dev, 1, &s.fences[idx], VK_TRUE, UINT64_MAX);   // this image's previous overlay pass is done
    d.ResetFences(d.dev, 1, &s.fences[idx]);
    VkCommandBuffer cmd = s.cmds[idx];
    d.ResetCommandBuffer(cmd, 0);
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    d.BeginCommandBuffer(cmd, &bi);
    VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    b.srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
    b.dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_READ_BIT | VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
    b.oldLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
    b.newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = s.images[idx];
    b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    d.CmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
                         0, 0, nullptr, 0, nullptr, 1, &b);
    VkRenderingAttachmentInfo att{VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO};
    att.imageView = s.views[idx];
    att.imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
    att.loadOp = VK_ATTACHMENT_LOAD_OP_LOAD;
    att.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
    VkRenderingInfo ri{VK_STRUCTURE_TYPE_RENDERING_INFO};
    ri.renderArea = {{0, 0}, s.extent};
    ri.layerCount = 1;
    ri.colorAttachmentCount = 1;
    ri.pColorAttachments = &att;
    d.CmdBeginRendering(cmd, &ri);
    ImGui_ImplVulkan_RenderDrawData(dd, cmd);
    d.CmdEndRendering(cmd);
    b.srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
    b.dstAccessMask = 0;
    b.oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
    b.newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
    d.CmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,
                         0, 0, nullptr, 0, nullptr, 1, &b);
    d.EndCommandBuffer(cmd);
    std::vector<VkPipelineStageFlags> stages(pi->waitSemaphoreCount, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT);
    VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    si.waitSemaphoreCount = pi->waitSemaphoreCount;     // the game's "rendering done"
    si.pWaitSemaphores = pi->pWaitSemaphores;
    si.pWaitDstStageMask = stages.data();
    si.commandBufferCount = 1;
    si.pCommandBuffers = &cmd;
    si.signalSemaphoreCount = 1;
    si.pSignalSemaphores = &s.done[idx];
    if (d.QueueSubmit(q, 1, &si, s.fences[idx]) != VK_SUCCESS) return VK_NULL_HANDLE;
    return s.done[idx];
}

VKAPI_ATTR VkResult VKAPI_CALL QueuePresentKHR(VkQueue q, const VkPresentInfoKHR* pi) {
    std::lock_guard<std::mutex> lk(g_Mu);
    Dev& d = g_Dev[Key(q)];
    VkSemaphore wait = VK_NULL_HANDLE;
    if (pi->swapchainCount >= 1) {
        auto it = d.swaps.find(pi->pSwapchains[0]);
        if (it != d.swaps.end()) wait = DrawOverlay(d, q, it->second, pi->pImageIndices[0], pi);
    }
    if (!wait) return d.QueuePresentKHR(q, pi);
    VkPresentInfoKHR p2 = *pi;
    p2.waitSemaphoreCount = 1;                           // (our submit already waited on the game's semaphores)
    p2.pWaitSemaphores = &wait;
    return d.QueuePresentKHR(q, &p2);
}

PFN_vkVoidFunction DeviceFn(const char* n) {
#define F(name) if (!strcmp(n, "vk" #name)) return (PFN_vkVoidFunction)name;
    F(DestroyDevice) F(GetDeviceQueue) F(GetDeviceQueue2) F(CreateSwapchainKHR) F(DestroySwapchainKHR) F(QueuePresentKHR)
#undef F
    return nullptr;
}

}  // namespace

// ---- loader interface ---------------------------------------------------------------------------------------------
LAYER_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL HookshotItemCtl_GetDeviceProcAddr(VkDevice dev, const char* n);

LAYER_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL HookshotItemCtl_GetInstanceProcAddr(VkInstance inst, const char* n) {
    if (!strcmp(n, "vkGetInstanceProcAddr")) return (PFN_vkVoidFunction)HookshotItemCtl_GetInstanceProcAddr;
    if (!strcmp(n, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)HookshotItemCtl_GetDeviceProcAddr;
    if (!strcmp(n, "vkCreateInstance")) return (PFN_vkVoidFunction)CreateInstance;
    if (!strcmp(n, "vkDestroyInstance")) return (PFN_vkVoidFunction)DestroyInstance;
    if (!strcmp(n, "vkCreateDevice")) return (PFN_vkVoidFunction)CreateDevice;
    if (PFN_vkVoidFunction f = DeviceFn(n)) return f;
    if (!inst) return nullptr;
    std::lock_guard<std::mutex> lk(g_Mu);
    auto it = g_Inst.find(Key(inst));
    return it == g_Inst.end() ? nullptr : it->second.gipa(inst, n);
}

LAYER_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL HookshotItemCtl_GetDeviceProcAddr(VkDevice dev, const char* n) {
    if (!strcmp(n, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)HookshotItemCtl_GetDeviceProcAddr;
    if (PFN_vkVoidFunction f = DeviceFn(n)) return f;
    std::lock_guard<std::mutex> lk(g_Mu);
    auto it = g_Dev.find(Key(dev));
    return it == g_Dev.end() ? nullptr : it->second.gdpa(dev, n);
}

LAYER_EXPORT VKAPI_ATTR VkResult VKAPI_CALL HookshotItemCtl_NegotiateLoaderLayerInterfaceVersion(VkNegotiateLayerInterface* v) {
    if (!v || v->sType != LAYER_NEGOTIATE_INTERFACE_STRUCT) return VK_ERROR_INITIALIZATION_FAILED;
    if (v->loaderLayerInterfaceVersion > 2) v->loaderLayerInterfaceVersion = 2;
    v->pfnGetInstanceProcAddr = HookshotItemCtl_GetInstanceProcAddr;
    v->pfnGetDeviceProcAddr = HookshotItemCtl_GetDeviceProcAddr;
    v->pfnGetPhysicalDeviceProcAddr = nullptr;
    return VK_SUCCESS;
}
