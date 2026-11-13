// Vulkan layer VK_LAYER_HOOKSHOT_item_controls: how the Item Controls code gets into the game on Linux and Windows.
// Hookshot and Longshot load only RomFS mods; the Vulkan loader loads this layer from the mod folder when the mod
// manager starts the game (VK_ADD_LAYER_PATH + VK_INSTANCE_LAYERS, set for the game's process).
// The layer starts the plugin (game hooks) when the game creates its Vulkan instance and draws nothing: the item boxes
// are the game's own (native_hud.cpp). Instance and device creation step down the layer chain; everything else goes
// straight to the next layer.
#include "platform.h"
#include "item_controls.h"
#include <vulkan/vk_layer.h>
#include <cstring>
#include <mutex>
#include <unordered_map>

#ifdef _WIN32
#define LAYER_EXPORT extern "C" __declspec(dllexport)
#else
#define LAYER_EXPORT extern "C" __attribute__((visibility("default")))
#endif

using plat::Log;

namespace {

void* Key(const void* dispatchable) { return *(void**)dispatchable; }

struct Inst {
    PFN_vkGetInstanceProcAddr gipa = nullptr;
    PFN_vkDestroyInstance DestroyInstance = nullptr;
};
struct Dev {
    PFN_vkGetDeviceProcAddr gdpa = nullptr;
    PFN_vkDestroyDevice DestroyDevice = nullptr;
};

std::mutex g_Mu;
std::unordered_map<void*, Inst> g_Inst;
std::unordered_map<void*, Dev> g_Dev;
bool g_PluginStarted = false;

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
    itemctl::Init(mod);
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

VKAPI_ATTR VkResult VKAPI_CALL CreateDevice(VkPhysicalDevice phys, const VkDeviceCreateInfo* ci, const VkAllocationCallbacks* alloc, VkDevice* out) {
    VkLayerDeviceCreateInfo* chain = DevChain(ci);
    if (!chain) return VK_ERROR_INITIALIZATION_FAILED;
    PFN_vkGetInstanceProcAddr gipa = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    PFN_vkGetDeviceProcAddr gdpa = chain->u.pLayerInfo->pfnNextGetDeviceProcAddr;
    chain->u.pLayerInfo = chain->u.pLayerInfo->pNext;
    auto create = (PFN_vkCreateDevice)gipa(VK_NULL_HANDLE, "vkCreateDevice");
    VkResult r = create(phys, ci, alloc, out);
    if (r != VK_SUCCESS) return r;
    std::lock_guard<std::mutex> lk(g_Mu);
    Dev& d = g_Dev[Key(*out)];
    d.gdpa = gdpa;
    d.DestroyDevice = (PFN_vkDestroyDevice)gdpa(*out, "vkDestroyDevice");
    return r;
}

VKAPI_ATTR void VKAPI_CALL DestroyDevice(VkDevice dev, const VkAllocationCallbacks* alloc) {
    PFN_vkDestroyDevice fn;
    {
        std::lock_guard<std::mutex> lk(g_Mu);
        auto it = g_Dev.find(Key(dev));
        if (it == g_Dev.end()) return;
        fn = it->second.DestroyDevice;
        g_Dev.erase(it);
    }
    fn(dev, alloc);
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
    if (!strcmp(n, "vkDestroyDevice")) return (PFN_vkVoidFunction)DestroyDevice;
    if (!inst) return nullptr;
    std::lock_guard<std::mutex> lk(g_Mu);
    auto it = g_Inst.find(Key(inst));
    return it == g_Inst.end() ? nullptr : it->second.gipa(inst, n);
}

LAYER_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL HookshotItemCtl_GetDeviceProcAddr(VkDevice dev, const char* n) {
    if (!strcmp(n, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)HookshotItemCtl_GetDeviceProcAddr;
    if (!strcmp(n, "vkDestroyDevice")) return (PFN_vkVoidFunction)DestroyDevice;
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
