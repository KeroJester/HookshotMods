// Minimal stand-in for boost::container::static_vector, enough for suyu's astc.cpp (built by mod_manager/tools).
#pragma once
#include <vector>
namespace boost::container {
template <class... A> struct static_vector_options { using type = void; };
template <std::size_t> struct inplace_alignment {};
template <bool> struct throw_on_overflow {};
template <class T, std::size_t N, class Opt = void>
class static_vector : public std::vector<T> {
public:
    static_vector() { this->reserve(N); }
};
}  // namespace boost::container
